"""
Tagging REST API exception handling utilities.
"""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """
    Return standard DRF errors for APIException and a generic 500 otherwise.
    """
    if isinstance(exc, APIException):
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
