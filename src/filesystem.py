#!/usr/bin/env python3

from api import JoplinApi
from argparse import ArgumentParser
import errno
import logging
from metadata import ItemType, JoplinMeta
import os
import pyfuse3
import stat
import trio
import unicodedata

try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()


log = logging.getLogger(__name__)

# Type def for Inode
Inode = int

class InodeMap:
    """
    Maps Joplin Items (identified by the Joplin Meta class) to Inodes and vice versa.
    If an item doesn not have an Inode, a valid Inode will be created.
    """
    def __init__(self):
        # maps ids to inode
        self._inode_map = {}
        # maps inode to metadata
        self._map_inode = {}
        self._current_inode = pyfuse3.ROOT_INODE

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
        meta =  self._map_inode.get(inode, None)
        if meta is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return meta


class JoplinFS(pyfuse3.Operations):
    def __init__(self, api: JoplinApi):
        super().__init__()
        self._inode_map = InodeMap()
        self.api = api

    async def _get_folders(self, parent_id: str):
        folders = await self.api.get("/folders", params={'fields': ['id', 'parent_id', 'title', 'user_updated_time', 'user_created_time']})
        return [(JoplinMeta(id=f['id'], type=ItemType.folder), f)
                for f in folders if f["parent_id"] == parent_id]
    async def _get_notes(self, parent_id: str):
        notes = await self.api.get(f"/folders/{parent_id}/notes", params={'fields': ['id', 'body', 'title', 'user_updated_time', 'user_created_time']})
        return [(JoplinMeta(id=n['id'], type=ItemType.note), n) for n in notes]

    async def _getattr_folder(self, folder):
        entry = pyfuse3.EntryAttributes()
        entry.st_mode = (stat.S_IFDIR | 0o755)
        entry.st_size = 4096

        return entry

    async def _getattr_note(self, note):
        entry = pyfuse3.EntryAttributes()
        entry.st_mode = (stat.S_IFREG | 0o644)
        entry.st_size = len(note["body"])

        return entry

    async def _getattr_resource(self, resource):
        entry = pyfuse3.EntryAttributes()
        entry.st_mode = (stat.S_IFREG | 0o644)
        entry.st_size = resource["size"]

        return entry

    async def _getattr(self, meta: JoplinMeta, data, inode: Inode):
        if meta.type == ItemType.note:
            entry = await self._getattr_note(data)
        elif meta.type == ItemType.resource:
            entry = await self._getattr_resource(data)
        else: # Folders and Tags are the same
            entry = await self._getattr_folder(data)
        # Last Access
        entry.st_atime_ns = data["user_updated_time"] * 10**6
        # Last Metadata change (Unix)
        # Creation time (Windows)
        # Joplin doesn't save metadata change time, so we use creation time
        entry.st_ctime_ns = data["user_created_time"] * 10**6
        # Last Modification
        entry.st_mtime_ns = data["user_updated_time"] * 10**6
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        entry.st_ino = inode

        return entry


    async def getattr(self, inode, ctx=None):
        log.info(f"Getattr inode {inode}")
        if inode == pyfuse3.ROOT_INODE:
            entry = pyfuse3.EntryAttributes()
            entry.st_mode = (stat.S_IFDIR | 0o755)
            entry.st_size = 4096
            return entry
        meta = self._inode_map.get_meta(inode)
        data = await self.api.get(meta.url, meta.params)

        return await self._getattr(meta, data, inode)


    async def lookup(self, parent_inode, name, ctx=None):
        log.info(f"Lookup parent_inode {parent_inode}, {name}")
        parent_meta = self._inode_map.get_meta(parent_inode)
        folders = await self._get_folders(parent_meta.id)
        notes = await self._get_notes(parent_meta.id)
        for meta, f in folders + notes:
            inode = self._inode_map.get_inode(meta)
            if bytes(f["title"], 'utf-8') == name:
                return await self._getattr(meta, f, inode)

        raise pyfuse3.FUSEError(errno.ENOENT)

    async def opendir(self, inode: Inode, ctx):
        log.info(f"Open dir inode {inode}")
        if inode == pyfuse3.ROOT_INODE:
            return inode
        meta = self._inode_map.get_meta(inode)
        if meta.type != ItemType.folder:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return inode

    def safe_filename(self, meta: JoplinMeta, data) -> bytes:
        name = data['title']

        not_allowed = "< > : \" / \\ | ? *".split()
        #
        name = unicodedata.normalize('NFKC', name)
        name = ''.join(c for c in name if c not in not_allowed)
        if len(name) > 253:
            name = name[:253]
        if meta.type == ItemType.note:
            name += ".md"

        return bytes(name, 'utf-8')

    async def readdir(self, inode: Inode, start_id: Inode, token):
        log.info(f"Readdir inode {inode}, {start_id} {token}")
        if inode == pyfuse3.ROOT_INODE:
            # TODO: Add tags folder and resource folder
            # "" means no parent_id aka root folders
            folders = await self._get_folders("")
            notes = []
        else:
            parent_meta = self._inode_map.get_meta(inode)
            folders = await self._get_folders(parent_meta.id)
            notes = await self._get_notes(parent_meta.id)

        for meta, f in folders + notes:
            inode = self._inode_map.get_inode(meta)
            if inode <= start_id:
                continue
            if not pyfuse3.readdir_reply(
                    token, self.safe_filename(meta, f), await self._getattr(meta, f, inode), inode):
                break

    async def open(self, inode, flags, ctx):
        log.info(f"Open inode {inode}")
        if inode == pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if flags & os.O_RDWR or flags & os.O_WRONLY:
            raise pyfuse3.FUSEError(errno.EACCES)
        return pyfuse3.FileInfo(fh=inode)

    async def read(self, inode: Inode, offset: int, size: int):
        log.info(f"Reading inode {inode}")
        if inode == pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)
        meta = self._inode_map.get_meta(inode)
        if meta.type not in [ItemType.note, ItemType.resource]:
            raise pyfuse3.FUSEError(errno.ENOENT)
        data = self._jget(meta.url, meta.params)
        return bytes(data['body'][offset:offset+size], 'utf-8')


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('mount', type=str, default=os.environ.get("JOPLINFS_MOUNT"),
                        help='Mountpoint for JoplinFS')
    parser.add_argument('--token', type=str, default=os.environ.get("JOPLINFS_TOKEN"),
                        help='The Joplin webclipper token')
    parser.add_argument('--debug-fuse', action='store_true', default=False,
                        help='Enable FUSE debugging output')
    options = parser.parse_args()

    api = JoplinApi(options.token)
    fs = JoplinFS(api)
    fuse_options = set(pyfuse3.default_options)
    fuse_options.add('fsname=joplinfs')
    if options.debug_fuse:
        fuse_options.add('debug')
    pyfuse3.init(fs, options.mount, fuse_options)
    try:
        trio.run(pyfuse3.main)
    finally:
        pyfuse3.close()



