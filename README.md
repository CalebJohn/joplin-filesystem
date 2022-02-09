# Joplin Filesystem (JoplinFS)

A Proof-Of-Concept FUSE filesytem that gives access to your Joplin notes.

Currently the Joplin Filesystem is readonly, but hopefully that will change in the next few days.

## Overview
This is a simple system that takes advantage of the hierarchical layout of notebooks in Joplin. Essentially it's a translation layer between the filesystem primitives and the Joplin API. 

## TODO
- [ ] Cache Joplin API calls
  - The current implementation doesn't do any sort of caching and as a result can end up hitting the Joplin API many times in a row looking for the same content
- [ ] Add Tags and Resource directories
- [ ] Translate links to be localized when opening notes
- [ ] Support writing to notes

## Limitations
- This only a *thin* wrapper around the Joplin API, so mass operations (like grep) will be very slow and are not recommended.
- This implementation uses the API for both reading and writing, a more performant implementation would be able to use the database for reading and only hit the API for a write.
- Metadata about notes and folders are stored in memory, this means the mount will start to consume large amounts of RAM if you have a lot of notes/folder (< 1,000,000 shouldn't be an issue for most computers)
