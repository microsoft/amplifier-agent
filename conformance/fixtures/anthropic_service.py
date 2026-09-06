"""Serve controlled Anthropic responses over the provider HTTP protocol."""

from .provider_services import provider_service


def anthropic_service(requests, failure=None, release=None):
    return provider_service("anthropic", requests, failure=failure, release=release)
