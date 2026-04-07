"""Signal handlers for tagging-related model updates."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from openedx_events.content_authoring.data import ContentObjectChangedData  # type: ignore[import-untyped]
from openedx_events.content_authoring.signals import CONTENT_OBJECT_ASSOCIATIONS_CHANGED  # type: ignore[import-untyped]

from openedx_tagging.models.base import ObjectTag, Tag

logger = logging.getLogger(__name__)


def _update_object_tags_in_search_index(tag):
    """Emit content association change events for all objects linked to `tag`."""
    # find object tags that are associated with the tag
    object_tags = ObjectTag.objects.filter(tag=tag)
    object_ids = object_tags.values_list("object_id", flat=True)

    for object_id in object_ids:
        logger.info("Updating search index for object_id: %s due to tag update: %s", object_id, tag.value)
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=object_id,
                changes=["tags"],
            ),
        )


@receiver(post_save, sender=Tag)
def tag_post_save(sender, **kwargs):  # pylint: disable=unused-argument
    """
    If a tag is updated, it will be updated in the search index.
    """
    instance = kwargs.get("instance", None)

    if kwargs.get("created", False):
        return
    else:
        _update_object_tags_in_search_index(tag=instance)
