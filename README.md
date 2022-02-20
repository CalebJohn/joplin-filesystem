# Joplin Filesystem (JoplinFS)

A Proof-Of-Concept FUSE filesytem that gives access to your Joplin notes.

Currently the Joplin Filesystem is readonly, but that will eventually change if there is interest.

## Overview
This is a simple system that takes advantage of the hierarchical layout of notebooks in Joplin. Essentially it's a translation layer between the filesystem primitives and the Joplin API. 

### Layout
```
$ tree <mount_folder>
.
├── .links
|   ├── <...>
├── .tags
|   ├── <...>
└── <folders>
```

The `.links` directory is a helper folder to make linking easy. It contains a symlink (named by id) to every note and resource. This makes it easy to take a Joplin link `[title](:/f9a1ba9631c346efa7ca1eb1d38dd64f)` and turn it into `[title](/path/to/mount/.links/f9a1ba9631c346efa7ca1eb1d38dd64f)`.

The `.tags` folder holds all the tags as subfolders. Each tag subfolder contains symlinks to the notes that have that tag.

The `<folders>` are just all the top level folders in Joplin. They represent the notebook/note structure of Joplin.

## Usage
Ensure you have a valid python installation (tested on python 3.7, but should work up to 3.9).

Install dependencies, either

```
poetry install
```
or
```
pip install -r requirements.txt
```

For testing run with

```
python src/filesystem.py --mount <mount folder> --token <joplin webclipper token>
```

`mount` and `token` can alternatively be specified with the environment variable `JOPLINFS_MOUNT` and `JOPLINFS_TOKEN`.



## TODO
- [ ] Support writing to notes

## Limitations
- This only a *thin* wrapper around the Joplin API, so mass operations (like grep) will be very slow and are not recommended.
- This implementation uses the API for both reading and writing, a more performant implementation would be able to use the database for reading and only hit the API for a write.
- Metadata about notes and folders are stored in memory, this means the mount will start to consume large amounts of RAM if you have a lot of notes/folder (< 1,000,000 shouldn't be an issue for most computers)
- Resources are ignored and won't be viewable if the markdown is passed in to a renderer
