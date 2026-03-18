"""Plugin hook specifications — the contract all plugins must follow."""

from __future__ import annotations

import pluggy

hookspec = pluggy.HookspecMarker("lotse")
hookimpl = pluggy.HookimplMarker("lotse")


class LotsePluginSpec:
    """Hook specifications for Lotse plugins.

    Plugins implement these hooks to extend Lotse's behavior.
    All hooks are optional — implement only what you need.
    """

    @hookspec(firstresult=False)
    def pre_classify(self, content: str, path: str) -> str:
        """Called before classification. Can transform content.

        Args:
            content: Extracted text content
            path: Original file path or URI

        Returns:
            Transformed content string
        """

    @hookspec(firstresult=False)
    def post_classify(self, classification: object, path: str) -> None:
        """Called after classification. Can inspect or modify the result.

        Args:
            classification: The Classification result object
            path: Original file path or URI
        """

    @hookspec(firstresult=False)
    def custom_route(self, path: str, classification: object) -> dict | None:
        """Called during routing. Return a dict to handle routing yourself.

        Args:
            path: File path to route
            classification: The Classification result

        Returns:
            Dict with 'destination' and 'message' keys, or None to skip
        """

    @hookspec(firstresult=False)
    def on_routed(self, path: str, destination: str, route_name: str) -> None:
        """Called after an item is successfully routed.

        Args:
            path: Original file path
            destination: Where the file was routed to
            route_name: Name of the route that matched
        """
