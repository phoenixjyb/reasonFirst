from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp.server import MCPServer

from gitlab_agent import bridge_mcp


class ServiceCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_opt_out_preserves_both_tool_catalogs(self):
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                controller = SimpleNamespace(managed_startup_state=None)
                ordinary = bridge_mcp.build_server(
                    read_only_mode=read_only, controller=controller,
                )
                managed = bridge_mcp.build_server(
                    read_only_mode=read_only, controller=controller,
                    subscriptions=False,
                )
                expected = [tool.model_dump(mode="json", by_alias=True)
                            for tool in await ordinary.list_tools()]
                actual = [tool.model_dump(mode="json", by_alias=True)
                          for tool in await managed.list_tools()]
                self.assertEqual(actual, expected)
                self.assertEqual(managed.instructions, ordinary.instructions)
                self.assertIs(managed._reasonfirst_controller, controller)
                ordinary_methods = set(ordinary._lowlevel_server._request_handlers)
                managed_methods = set(managed._lowlevel_server._request_handlers)
                self.assertEqual(ordinary_methods - managed_methods,
                                 {"subscriptions/listen"})
                self.assertFalse(managed_methods - ordinary_methods)

                before = ordinary._lowlevel_server.get_capabilities(
                    None, None, protocol_version="2026-07-28",
                ).model_dump(mode="json", by_alias=True, exclude_none=True)
                after = managed._lowlevel_server.get_capabilities(
                    None, None, protocol_version="2026-07-28",
                ).model_dump(mode="json", by_alias=True, exclude_none=True)
                for section in ("prompts", "resources", "tools"):
                    self.assertIs(before[section]["listChanged"], True)
                    self.assertIs(after[section]["listChanged"], False)
                self.assertIs(before["resources"]["subscribe"], True)
                self.assertIs(after["resources"]["subscribe"], False)

    async def test_default_does_not_add_an_sdk_constructor_option(self):
        controller = SimpleNamespace(managed_startup_state=None)
        with patch("mcp.server.MCPServer", wraps=MCPServer) as factory:
            bridge_mcp.build_server(read_only_mode=True, controller=controller)
            self.assertNotIn("subscriptions", factory.call_args.kwargs)
            bridge_mcp.build_server(read_only_mode=True, controller=controller,
                                    subscriptions=None)
            self.assertNotIn("subscriptions", factory.call_args.kwargs)
            bridge_mcp.build_server(read_only_mode=True, controller=controller,
                                    subscriptions=False)
            self.assertIs(factory.call_args.kwargs["subscriptions"], False)

    async def test_invalid_subscription_option_fails_before_controller_creation(self):
        with patch.object(bridge_mcp, "BridgeController") as controller:
            for value in (True, 0, 1, "false", object()):
                with self.subTest(value=type(value).__name__):
                    with self.assertRaisesRegex(ValueError,
                                                "subscriptions must be None or False"):
                        bridge_mcp.build_server(subscriptions=value)
            controller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
