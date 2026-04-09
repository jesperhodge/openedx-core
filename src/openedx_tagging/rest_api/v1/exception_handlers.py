"""
Tagging REST API exception handling utilities.
"""

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """
    Return standard DRF errors for APIException and a generic 500 otherwise.
    """
    # For exceptions expected by DRF return the standard DRF error response:
    # Instances of APIException, subclasses of APIException, Django's Http404 exception, and Django's PermissionDenied exception.
    is_expected_exception = isinstance(
        exc, (APIException, Http404, PermissionDenied)
    )
    if is_expected_exception:
        return exception_handler(exc, context)

    return Response(
        {"detail": "An unexpected error occurred while processing the request."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


class TaggingExceptionHandlerMixin:
    """
    Scope custom exception handling to tagging API views only.
    """

    def get_exception_handler(self):
        return custom_exception_handler
