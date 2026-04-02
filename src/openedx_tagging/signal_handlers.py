from django.dispatch import receiver
from django.db.models.signals import post_save

from openedx_tagging.models.base import ObjectTag, Tag
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
)
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
)

# Set up logging with INFO level and a specific format
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def _update_object_tags_in_search_index(tag):
    # find object tags that are associated with the tag
    object_tags = ObjectTag.objects.filter(tag=tag)
    object_ids = object_tags.values_list("object_id", flat=True)

    logger.info("Update signal called")
    for object_id in object_ids:
        logger.info(f"Updating search index for object_id: {object_id} due to tag update: {tag.name}")
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=object_id,
                changes=["collections"],
            ),
        )

@receiver(post_save, sender=Tag, uid="openedx_tagging.tag_post_save")
def tag_post_save(sender, instance, created, **kwargs):
    """
    If a tag is updated, it will be updated in the search index.
    """
    if created:
        return
    else:
        _update_object_tags_in_search_index(tag=instance)
