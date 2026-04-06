from django.dispatch import receiver
from django.db.models.signals import post_save

from openedx_tagging.models.base import ObjectTag, Tag
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
)
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
)

import logging

logger = logging.getLogger(__name__)

def _update_object_tags_in_search_index(tag):
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
def tag_post_save(sender, instance, created, **kwargs):
    """
    If a tag is updated, it will be updated in the search index.
    """
    if created:
        return
    else:
        _update_object_tags_in_search_index(tag=instance)
