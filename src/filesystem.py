#!/usr/bin/env python3

from api import JoplinApi
from argparse import ArgumentParser
import errno
import logging
from bridge import JoplinBridge, JoplinMeta, ItemType
import os
import pyfuse3
import stat
import trio

try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()


logging.basicConfig(format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Type def for Inode
Inode = int


class JoplinFS(pyfuse3.Operations):
    def __init__(self, api: JoplinApi, bridge: JoplinBridge):
        super().__init__()
        self._inode_map = bridge
        self.api = api
        print(dir(self))

    async def _getattr(self, meta: JoplinMeta, inode: Inode):
        entry = pyfuse3.EntryAttributes()
        entry.st_mode = meta.mode
        entry.st_size = meta.size
        # Last Access
        entry.st_atime_ns = meta.updated * 10**6
        # Last Metadata change (Unix)
        # Creation time (Windows)
        # Joplin doesn't save metadata change time, so we use creation time
        entry.st_ctime_ns = meta.created * 10**6
        # Last Modification
        entry.st_mtime_ns = meta.updated * 10**6
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
        meta = await self._inode_map.get_meta(inode)

        return await self._getattr(meta, inode)

    async def lookup(self, parent_inode, name, ctx=None):
        log.info(f"Lookup parent_inode {parent_inode}, {name}")
        parent_meta = await self._inode_map.get_meta(parent_inode)

        for inode in parent_meta.children:
            m = await self._inode_map.get_meta(inode)
            if m.safe_filename == name:
                return await self._getattr(m, inode)

        raise pyfuse3.FUSEError(errno.ENOENT)

    async def opendir(self, inode: Inode, ctx):
        log.info(f"Open dir inode {inode}")
        if inode == pyfuse3.ROOT_INODE:
            return inode
        meta = await self._inode_map.get_meta(inode)
        if meta.type != ItemType.folder:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return inode

    async def readdir(self, inode: Inode, start_id: Inode, token):
        log.info(f"Readdir inode {inode}, {start_id} {token}")
        # TODO: Add tags folder and resource folder
        meta = await self._inode_map.get_meta(inode)

        for inode in meta.children:
            if inode <= start_id:
                continue
            m = await self._inode_map.get_meta(inode)
            if not pyfuse3.readdir_reply(token, m.safe_filename, await self._getattr(m, inode), inode):
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
        return await self._inode_map.read(inode, offset, size)


if __name__ == "__main__":
    async def main(bridge):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(pyfuse3.main)
            nursery.start_soon(bridge.check_for_update)

    parser = ArgumentParser()
    parser.add_argument('mount', type=str, default=os.environ.get("JOPLINFS_MOUNT"),
                        help='Mountpoint for JoplinFS')
    parser.add_argument('--token', type=str, default=os.environ.get("JOPLINFS_TOKEN"),
                        help='The Joplin webclipper token')
    parser.add_argument('--debug-fuse', action='store_true', default=False,
                        help='Enable FUSE debugging output')
    options = parser.parse_args()

    api = JoplinApi(options.token)
    bridge = JoplinBridge(api)
    fs = JoplinFS(api, bridge)
    fuse_options = set(pyfuse3.default_options)
    fuse_options.add('fsname=joplinfs')
    if options.debug_fuse:
        fuse_options.add('debug')
    pyfuse3.init(fs, options.mount, fuse_options)
    try:
        trio.run(main, bridge)
    finally:
        pyfuse3.close()



