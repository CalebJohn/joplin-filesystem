from dataclasses import dataclass
from enum import Enum, auto


class ItemType(Enum):
    folder = auto()
    note = auto()
    resource = auto()
    tag = auto()


@dataclass
class JoplinMeta:
    """
    This stores the metadata that is necessary to poll the Joplin API
    """
    id: str
    type: ItemType

    @property
    def url(self):
        if self.type == ItemType.folder:
            return f"folders/{self.id}"
        elif self.type == ItemType.note:
            return f"notes/{self.id}"
        elif self.type == ItemType.resource:
            return f"resources/{self.id}"
        elif self.type == ItemType.tag:
            return f"tags/{self.id}"

        # To satisfy the type checker
        return ''

    @property
    def params(self):
        if self.type == ItemType.folder:
            return {'fields': ['id', 'parent_id', 'title', 'user_updated_time', 'user_created_time']}
        elif self.type == ItemType.note:
            return {'fields': ['id', 'parent_id', 'title', 'body', 'user_updated_time', 'user_created_time']}
        elif self.type == ItemType.resource:
            return {'fields': ['id', 'size' 'title', 'user_updated_time', 'user_created_time']}
        elif self.type == ItemType.tag:
            return {'fields': ['id', 'title', 'user_updated_time', 'user_created_time']}

        # To satisfy the type checker
        return {}
