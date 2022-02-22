from api import JoplinApi
from dataclasses import dataclass, field
from enum import Enum
import errno
import pyfuse3
import stat
import trio
from typing import List
import unicodedata
import logging
import re

logging.basicConfig(format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)
# log.setLevel(logging.DEBUG)

joplin_internal_link_regex = re.compile(r":\/([0-9a-fA-F]+)")

# Type def for Inode
Inode = int

# https://github.com/laurent22/joplin/blob/dev/readme/api/references/rest_api.md#item-type-ids
class ItemType(Enum):
    note = 1
    folder = 2
    setting = 3
    resource = 4
    tag = 5
    note_tag = 6
    search = 7
    alarm = 8
    master_key = 9
    item_change = 10
    note_resource = 11
    resource_local_state = 12
    revision = 13
    migration = 14
    smart_filter = 15
    command = 16
    virtual = 99


class EventType(Enum):
    created = 1
    updated = 2
    deleted = 3


@dataclass
class JoplinMeta:
    """
    This stores the metadata that is necessary to poll the Joplin API
    """
    id: str
    type: ItemType
    title: str
    updated: int
    created: int
    byte_size: int = 0 # used for resources only
    children: List = field(default_factory=list) # used for folders
    parent: Inode = 0

    @property
    def mode(self):
        if self.type in [ItemType.folder, ItemType.tag, ItemType.virtual]:
            return stat.S_IFDIR | 0o755
        elif self.type == ItemType.note:
            return stat.S_IFREG | 0o644
        elif self.type == ItemType.resource:
            return stat.S_IFREG | 0o644

        # To satisfy the type checker
        return 0

    @property
    def sym_mode(self):
        return (stat.S_IFLNK | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP |
                stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)

    @property
    def size(self) -> int:
        if self.type in [ItemType.folder, ItemType.tag, ItemType.virtual]:
            return 4096
        elif self.type == ItemType.note:
            return self.byte_size if self.byte_size else 1024
        elif self.type == ItemType.resource:
            return self.byte_size

        # To satisfy the type checker
        return 0

    @property
    def safe_filename(self) -> bytes:
        name = self.title

        not_allowed = "< > : \" / \\ | ? *".split()
        #
        name = unicodedata.normalize('NFKC', name)
        name = ''.join(c for c in name if c not in not_allowed)
        if len(name) > 248:
            name = name[:248]
        # Make all names unique
        # TODO: Add an option to remove this
        name += f"_{self.id[:4]}"
        if self.type == ItemType.note:
            name += ".md"

        return bytes(name, 'utf-8')


class JoplinBridge:
    """
    Maps Joplin Items (identified by the Joplin Meta class) to Inodes and vice versa.
    If an item doesn not have an Inode, a valid Inode will be created.
    """
    def __init__(self, api: JoplinApi, mount: str):
        # maps ids to inode
        self._inode_map = {}
        self._current_inode = pyfuse3.ROOT_INODE
        # maps inode to metadata
        self._map_inode = {
                self._current_inode: JoplinMeta(id='', type=ItemType.folder, title='rootfs', updated=0, created=0)
        }
        self.api = api
        self._mount_substitution = f"file://{mount}/.links/\\1"
        self._tree = {}
        self._update_cursor = None
        self.update_check_period = 3 #s
        trio.run(self._construct_map)

    async def _get_all_folders(self) -> list:
        folders = await self.api.get("/folders", params={'fields': ['id', 'parent_id', 'title', 'user_updated_time', 'user_created_time']})
        return folders
    async def _get_folders(self, parent_id: str):
        folders = await self._get_all_folders()
        return [JoplinMeta(id=f['id'], type=ItemType.folder, updated=f['user_updated_time'], created=f['user_created_time'], title=f['title'])
                for f in folders if f["parent_id"] == parent_id]
    async def _get_notes(self, parent_id: str):
        notes = await self.api.get(f"/folders/{parent_id}/notes", params={'fields': ['id', 'body', 'title', 'user_updated_time', 'user_created_time']})
        return [JoplinMeta(id=n['id'], type=ItemType.note, updated=n['user_updated_time'], created=n['user_created_time'], title=n['title']) for n in notes]
    async def _get_resources(self):
        resources = await self.api.get(f"/resources", params={'fields':
            ['id', 'size', 'title', 'user_updated_time', 'user_created_time']})
        return [JoplinMeta(id=r['id'], type=ItemType.resource, byte_size=r['size'], updated=r['user_updated_time'], created=r['user_created_time'], title=r['title'])
                for r in resources]
    async def _get_tags(self):
        tags = await self.api.get(f"/tags", params={'fields': ['id', 'title', 'user_updated_time', 'user_created_time']})
        return [JoplinMeta(id=t['id'], type=ItemType.tag, updated=t['user_updated_time'], created=t['user_created_time'], title=t['title'])
                for t in tags]
    async def _get_tag_notes(self, id: str):
        tags = await self.api.get(f"/tags/{id}/notes", params={'fields': ['id']})
        return [tag['id'] for tag in tags]
    async def _get_events(self):
        if self._update_cursor is None:
            self._update_cursor = await self.api.get_cursor()

        events = await self.api.get(f"/events", params={'cursor': self._update_cursor, 'fields': ['id', 'item_id', 'item_type', 'created_time', 'type']})
        return events

    async def _get_folder(self, id: str):
        folder = await self.api.get(f"/folders/{id}", params={'fields': ['id', 'parent_id', 'title', 'user_updated_time', 'user_created_time']})
        return folder
    async def _get_note(self, id: str, body: bool = False):
        fields = ['id', 'parent_id', 'title', 'user_updated_time', 'user_created_time']
        if body:
            fields.append('body')
        note = await self.api.get(f"/notes/{id}", params={'fields': fields})
        return note
    async def _get_tag(self, id: str):
        tag = await self.api.get(f"tags/{id}", params={'fields':  ['id', 'title', 'user_updated_time', 'user_created_time']})
        return tag
    async def _get_resource(self, id: str):
        resource = await self.api.get(f"resources/{id}/file", params={'fields': ['id', 'size' 'title', 'user_updated_time', 'user_created_time']})
        return resource

    def get_inode(self, meta: JoplinMeta) -> Inode:
        """
        Takes a Joplin Meta object and returns a valid inode (a new one, or the corresponding one)
        """
        inode = self._inode_map.get(meta.id, None)
        if inode is None:
            self._current_inode += 1
            inode = self._current_inode
            self._inode_map[meta.id] = inode
            self._map_inode[inode] = meta
        
        return inode

    # Takes an inode and returns the corresponding Joplin Meta object
    def get_meta(self, inode: Inode) -> JoplinMeta:
        """
        Returns the Joplin Meta data associated with an inode, or raise ENOENT
        """
        meta = self._map_inode.get(inode, None)
        if meta is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return meta

    async def read(self, inode: Inode, offset: int, size: int) -> bytes:
        """
        Loads the body of a specific note, and returns it trimmed to the offset and size
        """
        meta = self.get_meta(inode)
        if meta.type not in [ItemType.note, ItemType.resource]:
            raise pyfuse3.FUSEError(errno.ENOENT)

        if meta.type == ItemType.note:
            note = await self._get_note(meta.id, body=True)
            body = re.sub(joplin_internal_link_regex, self._mount_substitution, note['body']) # type: ignore
        else:
            body = await self._get_resource(meta.id)
        offset = min(len(body), offset)
        extent = min(len(body), offset+size)
        meta.byte_size = len(body)
        if meta.type == ItemType.note:
            return bytes(body[offset:extent], 'utf-8') # type: ignore
        else:
            return body[offset:extent]

    async def _construct_map(self):
        """
        Builds an internal representation of the folder/note tree
        """
        root = self._map_inode[pyfuse3.ROOT_INODE]
        folders = await self._get_all_folders()

        for f in folders:
            meta = JoplinMeta(id=f['id'], type=ItemType.folder, updated=f['user_updated_time'], created=f['user_created_time'], title=f['title'])
            # Ensure there is an inode for this folder (ignore the result)
            f_inode = self.get_inode(meta)
            # Ensure we are using the cached meta
            meta = self.get_meta(f_inode)
            notes = await self._get_notes(meta.id)
            for i in notes:
                meta.children.append(self.get_inode(i))
                i.parent = f_inode

        ## Resources
        # resources = await self._get_resources()
        # for r in resources:
        #     # We just want to store references to these resources, they'll be picked up by the
        #     # .links folder later
        #     self.get_inode(r)

        ## Virtual Folders
        # LINKS
        # This is where we'll be keeping every single Joplin item, keyed by id
        # This is a conveniant place to link to
        links_folder = JoplinMeta(id='links', type=ItemType.virtual, title=".links", created=0, updated=0, parent=pyfuse3.ROOT_INODE)
        links_inode = self.get_inode(links_folder)
        root.children.append(links_inode)
        # Tags
        tags_folder = JoplinMeta(id='tags', type=ItemType.virtual, title=".tags", created=0, updated=0, parent=pyfuse3.ROOT_INODE)
        tags_inode = self.get_inode(tags_folder)
        root.children.append(tags_inode)

    async def check_for_update(self):
        """
        Reads the latest events from the Joplin api, and updates the internal tree accordingly
        """
        while 1:
            log.debug("Entering update check")
            root = self._map_inode[pyfuse3.ROOT_INODE]
            events = await self._get_events()
            for event in events:
                await self._apply_event(event)
                if int(event['created_time']) > root.updated:
                    root.updated = int(event['created_time'])
                if int(event['id']) > self._update_cursor:
                    self._update_cursor = int(event['id'])
            await trio.sleep(self.update_check_period)

    async def _apply_event(self, event):
        if event['item_type'] != ItemType.note.value:
            # Only notes are supported by /event
            # https://github.com/laurent22/joplin/blob/dev/readme/api/references/rest_api.md#events
            return

        inode = self._inode_map.get(event['item_id'], None)
        if event['type'] == EventType.deleted.value:
            if inode is not None:
                meta = self._map_inode.get(inode, None)

                # We are safe to start cleaning up the inode related stuff (since it exists)
                del self._inode_map[event['item_id']]
                safe_invalidate_inode(inode)

                if meta is None:
                    return

                if meta.parent:
                    pyfuse3.invalidate_entry(meta.parent, meta.safe_filename)
                    parent = self._map_inode.get(meta.parent)
                    if parent:
                        parent.children.remove(inode)
                del self._map_inode[inode]

        else:
            item = await self._get_note(event['item_id'])

            if inode is not None and event['type'] == EventType.updated.value:
                meta = self._map_inode.get(inode, None)
                if meta is None: return

                safe_invalidate_inode(inode)
                meta.updated = item['user_updated_time']
                meta.created = item['user_created_time']
                meta.title = item['title']

                new_parent_inode = self._inode_map.get(item['parent_id'], None)
                # TODO this should never happen, what is a sane response?
                if new_parent_inode is None: return

                if new_parent_inode != meta.parent:
                    old_parent_meta = self._map_inode.get(meta.parent, None)
                    if old_parent_meta is None: return 
                    old_parent_meta.children.remove(inode)
                    new_parent_meta = self._map_inode.get(new_parent_inode, None)
                    if new_parent_meta is None: return
                    new_parent_meta.children.append(inode)
                    safe_invalidate_inode(meta.parent)
                    safe_invalidate_inode(new_parent_inode)

            elif inode is None or event['type'] == EventType.created.value:
                meta = JoplinMeta(id=item['id'], type=ItemType(event['item_type']), title=item['title'], updated=item['user_updated_time'], created=item['user_created_time'])
                inode = self.get_inode(meta)
                new_parent_inode = self._inode_map.get(item['parent_id'], None)
                # TODO this should never happen, what is a sane response?
                if new_parent_inode is None: return
                new_parent_meta = self._map_inode.get(new_parent_inode, None)
                if new_parent_meta is None: return
                new_parent_meta.children.append(inode)
                safe_invalidate_inode(new_parent_inode)

    async def get_children(self, meta: JoplinMeta):
        children = meta.children[:]
        if meta.type == ItemType.virtual:
            if meta.id == "links":
                children = [inode for inode in self._inode_map.values() if inode != pyfuse3.ROOT_INODE]
            elif meta.id == "tags":
                tags = await self._get_tags()
                meta.children = []
                for t in tags:
                    # Some tags stick around in the database, but have no notes
                    # we need to filter those away
                    notes = await self._get_tag_notes(t.id)
                    if len(notes) > 0:
                        t_inode = self.get_inode(t)
                        meta.children.append(t_inode)
                children = meta.children
        elif meta.type == ItemType.tag:
            notes = await self._get_tag_notes(meta.id)
            children = [self._inode_map[id] for id in notes]
        elif meta.type == ItemType.folder:
            # Notebooks are added on the fly because the internal representation can't be
            # updated by the events endpoint (tags have the same issue)
            sub_folders = [f for f in await self._get_folders(meta.id)]
            for i in sub_folders:
                children.append(self.get_inode(i))

        return sorted(children)


def safe_invalidate_inode(inode: Inode):
    """
    Sometimes we'll find ourselves in a situation where an inode has been created in the bridge,
    but is not known to the kernel, this will cause a FileNotFoundError.
    CAUTION: It's dangerous to use this everywhere, so use it with care
    """
    try:
        pyfuse3.invalidate_inode(inode)
    except FileNotFoundError:
        pass

