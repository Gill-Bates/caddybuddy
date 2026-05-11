#!/usr/bin/env python3
#
# tests/test_deployment_engine.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError
from app.models.entities import DeploymentStatus
from app.services.caddy import CaddyServiceError
from app.services.deployment_engine import DeploymentError, DeploymentResult, DeploymentEngine
from sqlalchemy.orm.exc import StaleDataError


class _AsyncNullContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeDeployment:
    def __init__(self, *, site, server) -> None:
        self.id = 99
        self.status = DeploymentStatus.PENDING
        self.site = site
        self.server = server
        self.site_id = site.id
        self.server_id = server.id
        self.validation_output = None
        self.deployment_error = None
        self.deployed_at = None
        self.deployed_by = None

    def mark_deploying(self) -> None:
        self.status = DeploymentStatus.DEPLOYING

    def mark_failed(self, error: str) -> None:
        self.status = DeploymentStatus.FAILED
        self.deployment_error = error

    def mark_deployed(self, deployed_by: str | None = None) -> None:
        self.status = DeploymentStatus.DEPLOYED
        self.deployed_by = deployed_by


class DeploymentEngineRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_deployment_reuses_execute_deployment_path(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(flush=AsyncMock())
        deployment = SimpleNamespace(
            status=DeploymentStatus.FAILED,
            validation_output="old validation output",
            deployment_error="old deployment error",
        )
        expected = DeploymentResult(
            success=True,
            deployment=deployment,
            message="retried",
        )
        engine.execute_deployment = AsyncMock(return_value=expected)

        result = await engine.retry_deployment(
            session,
            deployment,
            deployed_by="tester",
        )

        self.assertIs(result, expected)
        self.assertEqual(deployment.status, DeploymentStatus.PENDING)
        self.assertIsNone(deployment.validation_output)
        self.assertIsNone(deployment.deployment_error)
        session.flush.assert_awaited_once()
        engine.execute_deployment.assert_awaited_once_with(
            session,
            deployment,
            deployed_by="tester",
        )

    async def test_retry_deployment_rejects_non_retryable_status(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(flush=AsyncMock())
        deployment = SimpleNamespace(
            status=DeploymentStatus.DEPLOYED,
            validation_output=None,
            deployment_error=None,
        )
        engine.execute_deployment = AsyncMock()

        result = await engine.retry_deployment(session, deployment)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Invalid state for retry")
        session.flush.assert_not_awaited()
        engine.execute_deployment.assert_not_awaited()


class DeploymentEngineImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_deployment_resolves_imported_caddyfiles(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()
        template = SimpleNamespace(
            name="app-site",
            caddyfile="import security_headers\nreverse_proxy {{upstream}}",
            variables={},
        )
        imported_template = SimpleNamespace(
            name="security_headers",
            caddyfile="header {\n    -Server\n}",
            variables={},
        )
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=True,
            variables={"upstream": "127.0.0.1:8080"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
            config_template=template,
        )
        server = SimpleNamespace(id=2, name="srv-1", active=True)
        deployment = SimpleNamespace(id=3)

        with (
            patch("app.services.deployment_engine.config_template_repository.get_by_name", AsyncMock(return_value=imported_template)),
            patch("app.services.deployment_engine.deployment_repository.create", AsyncMock(return_value=deployment)) as create_deployment,
        ):
            result = await engine.create_deployment(session, site=site, server=server)

        self.assertIs(result, deployment)
        create_kwargs = create_deployment.await_args.kwargs
        self.assertEqual(create_kwargs["site_id"], 1)
        self.assertEqual(create_kwargs["server_id"], 2)
        self.assertIn("header {", create_kwargs["rendered_config"])
        self.assertIn("reverse_proxy 127.0.0.1:8080", create_kwargs["rendered_config"])
        self.assertNotIn("import security_headers", create_kwargs["rendered_config"])

    async def test_create_deployment_fails_when_imported_caddyfile_is_missing(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()
        template = SimpleNamespace(
            name="app-site",
            caddyfile="import custom_missing_snippet\nreverse_proxy {{upstream}}",
            variables={},
        )
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=True,
            variables={"upstream": "127.0.0.1:8080"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
            config_template=template,
        )
        server = SimpleNamespace(id=2, name="srv-1", active=True)

        with patch("app.services.deployment_engine.config_template_repository.get_by_name", AsyncMock(return_value=None)):
            with self.assertRaises(DeploymentError) as exc:
                await engine.create_deployment(session, site=site, server=server)

        self.assertIn("Imported Caddyfile 'custom_missing_snippet' not found", str(exc.exception))

    async def test_create_deployment_uses_builtin_security_headers_when_missing_in_db(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()
        template = SimpleNamespace(
            name="app-site",
            caddyfile="import security_headers\nreverse_proxy {{upstream}}",
            variables={},
        )
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=True,
            variables={"upstream": "127.0.0.1:8080"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
            config_template=template,
        )
        server = SimpleNamespace(id=2, name="srv-1", active=True)
        deployment = SimpleNamespace(id=3)

        with (
            patch("app.services.deployment_engine.config_template_repository.get_by_name", AsyncMock(return_value=None)),
            patch("app.services.deployment_engine.deployment_repository.create", AsyncMock(return_value=deployment)) as create_deployment,
        ):
            result = await engine.create_deployment(session, site=site, server=server)

        self.assertIs(result, deployment)
        rendered_config = create_deployment.await_args.kwargs["rendered_config"]
        self.assertIn("Strict-Transport-Security", rendered_config)
        self.assertIn("reverse_proxy 127.0.0.1:8080", rendered_config)

    async def test_create_deployment_forwards_deployed_by_to_repository(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()
        template = SimpleNamespace(
            name="app-site",
            caddyfile="respond ok",
            variables={},
        )
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=True,
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
            config_template=template,
        )
        server = SimpleNamespace(id=2, name="srv-1", active=True)
        deployment = SimpleNamespace(id=3)

        with patch("app.services.deployment_engine.deployment_repository.create", AsyncMock(return_value=deployment)) as create_deployment:
            await engine.create_deployment(session, site=site, server=server, deployed_by="alice")

        self.assertEqual(create_deployment.await_args.kwargs["deployed_by"], "alice")

    async def test_create_deployment_rejects_excessive_import_depth(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()
        template_chain = {
            f"snippet-{index}": SimpleNamespace(
                name=f"snippet-{index}",
                caddyfile=(f"import snippet-{index + 1}" if index < 10 else "respond ok"),
                variables={},
            )
            for index in range(11)
        }
        root_template = SimpleNamespace(
            name="root-template",
            caddyfile="import snippet-0",
            variables={},
        )
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=True,
            variables={"upstream": "127.0.0.1:8080"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
            config_template=root_template,
        )
        server = SimpleNamespace(id=2, name="srv-1", active=True)

        with patch(
            "app.services.deployment_engine.config_template_repository.get_by_name",
            AsyncMock(side_effect=lambda _session, name: template_chain.get(name)),
        ):
            with self.assertRaisesRegex(DeploymentError, "Caddyfile import depth exceeded"):
                await engine.create_deployment(session, site=site, server=server)


class DeploymentEngineTemplateValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_template_for_save_renders_full_config_before_adapt(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()

        with (
            patch("app.services.deployment_engine.config_template_repository.get_by_name", AsyncMock(return_value=None)),
            patch("app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json", AsyncMock(return_value={})) as adapt,
        ):
            await engine.validate_template_for_save(
                session,
                name="app-site",
                caddyfile="import security_headers\nreverse_proxy {{upstream}}",
            )

        rendered_config = adapt.await_args.args[0]
        self.assertIn("example.com", rendered_config)
        self.assertIn("Strict-Transport-Security", rendered_config)
        self.assertIn("reverse_proxy 127.0.0.1:8080", rendered_config)

    async def test_validate_template_for_save_rejects_missing_variables(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()

        with self.assertRaises(DeploymentError) as exc:
            await engine.validate_template_for_save(
                session,
                name="app-site",
                caddyfile="reverse_proxy {{backend}}",
            )

        self.assertIn("Configuration rendering failed", str(exc.exception))
        self.assertIn("backend", str(exc.exception))

    async def test_validate_template_for_save_surfaces_caddy_adapt_errors(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace()

        with patch(
            "app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json",
            AsyncMock(side_effect=CaddyServiceError("unexpected token")),
        ):
            with self.assertRaises(CaddyServiceError) as exc:
                await engine.validate_template_for_save(
                    session,
                    name="app-site",
                    caddyfile="reverse_proxy {{upstream}}",
                )

        self.assertIn("unexpected token", str(exc.exception))


class DeploymentEngineExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_state_transition_surfaces_concurrent_modification(self) -> None:
        session = SimpleNamespace(flush=AsyncMock(side_effect=StaleDataError()), begin_nested=lambda: _AsyncNullContext())
        deployment = SimpleNamespace(id=42, status=DeploymentStatus.PENDING)

        with self.assertRaisesRegex(DeploymentError, "Concurrent modification of deployment 42"):
            await DeploymentEngine._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.DEPLOYING,
                lambda: setattr(deployment, "status", DeploymentStatus.DEPLOYING),
            )

    async def test_apply_state_transition_surfaces_constraint_violation(self) -> None:
        session = SimpleNamespace(
            flush=AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("boom"))),
            begin_nested=lambda: _AsyncNullContext(),
        )
        deployment = SimpleNamespace(id=42, status=DeploymentStatus.DEPLOYING)

        with self.assertRaisesRegex(DeploymentError, "Deployment constraint violation for deployment 42"):
            await DeploymentEngine._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.DEPLOYED,
                lambda: setattr(deployment, "status", DeploymentStatus.DEPLOYED),
            )

    async def test_execute_deployment_loads_complete_server_config(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(flush=AsyncMock())
        server = SimpleNamespace(id=7, name="srv-1")
        current_site = SimpleNamespace(
            id=2,
            domain="beta.example.com",
            config_template=SimpleNamespace(name="beta", caddyfile="respond beta", variables={}),
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        existing_site = SimpleNamespace(
            id=1,
            domain="alpha.example.com",
            config_template=SimpleNamespace(name="alpha", caddyfile="respond alpha", variables={}),
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        deployment = _FakeDeployment(site=current_site, server=server)
        current_runtime_config = {
            "admin": {"listen": "0.0.0.0:2019"},
            "logging": {"logs": {"existing": {"writer": {"output": "stderr"}}}},
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "match": [{"host": ["caddy.sv2.cirrio.de"]}],
                                    "handle": [{"handler": "static_response", "body": "ui"}],
                                }
                            ],
                            "logs": {"logger_names": {"caddy.sv2.cirrio.de": ["existing"]}},
                        }
                    }
                }
            },
        }
        managed_payload = {
            "logging": {"logs": {"log0": {"writer": {"output": "stdout"}}}},
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "match": [{"host": ["alpha.example.com"]}],
                                    "handle": [{"handler": "static_response", "body": "alpha"}],
                                },
                                {
                                    "match": [{"host": ["beta.example.com"]}],
                                    "handle": [{"handler": "static_response", "body": "beta"}],
                                },
                            ],
                            "logs": {
                                "logger_names": {
                                    "alpha.example.com": ["log0"],
                                    "beta.example.com": ["log0"],
                                }
                            },
                        }
                    }
                }
            },
        }

        with (
            patch("app.services.deployment_engine.site_repository.get_deployed_sites", new=AsyncMock(return_value=[existing_site])),
            patch("app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json", new=AsyncMock(return_value=managed_payload)) as adapt,
            patch("app.services.deployment_engine.caddy_service.fetch_config", new=AsyncMock(return_value=current_runtime_config)) as fetch_config,
            patch("app.services.deployment_engine.caddy_service.deploy_config", new=AsyncMock(return_value={"status": "ok"})) as deploy_config,
            patch("app.services.deployment_engine.deployment_repository.get_active_deployment", new=AsyncMock(return_value=None)),
            patch("app.services.deployment_engine.publish_resource_event", new=AsyncMock()),
        ):
            result = await engine.execute_deployment(session, deployment, deployed_by="alice")

        self.assertTrue(result.success)
        rendered_config = adapt.await_args.args[0]
        self.assertIn("alpha.example.com", rendered_config)
        self.assertIn("beta.example.com", rendered_config)
        self.assertLess(rendered_config.find("alpha.example.com"), rendered_config.find("beta.example.com"))
        fetch_config.assert_awaited_once_with(server)
        deployed_payload = deploy_config.await_args.args[1]
        self.assertEqual(deployed_payload["admin"]["listen"], "0.0.0.0:2019")
        deployed_routes = deployed_payload["apps"]["http"]["servers"]["srv0"]["routes"]
        deployed_hosts = [route["match"][0]["host"][0] for route in deployed_routes]
        self.assertEqual(
            deployed_hosts,
            ["caddy.sv2.cirrio.de", "alpha.example.com", "beta.example.com"],
        )

    async def test_execute_deployment_replaces_only_managed_domains_in_runtime_config(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(flush=AsyncMock())
        server = SimpleNamespace(id=7, name="srv-1")
        current_site = SimpleNamespace(
            id=2,
            domain="beta.example.com",
            config_template=SimpleNamespace(name="beta", caddyfile="respond beta", variables={}),
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        deployment = _FakeDeployment(site=current_site, server=server)
        existing_runtime_config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "match": [{"host": ["beta.example.com"]}],
                                    "handle": [{"handler": "static_response", "body": "old-beta"}],
                                },
                                {
                                    "match": [{"host": ["caddy.sv2.cirrio.de"]}],
                                    "handle": [{"handler": "static_response", "body": "ui"}],
                                },
                            ],
                        }
                    }
                }
            }
        }
        managed_payload = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "match": [{"host": ["beta.example.com"]}],
                                    "handle": [{"handler": "static_response", "body": "new-beta"}],
                                }
                            ],
                        }
                    }
                }
            }
        }

        with (
            patch("app.services.deployment_engine.site_repository.get_deployed_sites", new=AsyncMock(return_value=[])),
            patch("app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json", new=AsyncMock(return_value=managed_payload)),
            patch("app.services.deployment_engine.caddy_service.fetch_config", new=AsyncMock(return_value=existing_runtime_config)),
            patch("app.services.deployment_engine.caddy_service.deploy_config", new=AsyncMock(return_value={"status": "ok"})) as deploy_config,
            patch("app.services.deployment_engine.deployment_repository.get_active_deployment", new=AsyncMock(return_value=None)),
            patch("app.services.deployment_engine.publish_resource_event", new=AsyncMock()),
        ):
            result = await engine.execute_deployment(session, deployment, deployed_by="alice")

        self.assertTrue(result.success)
        deployed_routes = deploy_config.await_args.args[1]["apps"]["http"]["servers"]["srv0"]["routes"]
        deployed_bodies = [route["handle"][0]["body"] for route in deployed_routes]
        self.assertEqual(deployed_bodies, ["ui", "new-beta"])

    async def test_execute_deployment_propagates_unexpected_render_exception(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(flush=AsyncMock(), begin_nested=lambda: _AsyncNullContext())
        server = SimpleNamespace(id=7, name="srv-1")
        current_site = SimpleNamespace(
            id=2,
            domain="beta.example.com",
            config_template=SimpleNamespace(name="beta", caddyfile="respond beta", variables={}),
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        deployment = _FakeDeployment(site=current_site, server=server)

        with (
            patch.object(engine, "_render_server_config", new=AsyncMock(return_value="beta.example.com { respond beta }")),
            patch("app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json", new=AsyncMock(return_value={"apps": {}})),
            patch(
                "app.services.deployment_engine.caddy_service.fetch_config",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await engine.execute_deployment(session, deployment, deployed_by="alice")

        self.assertEqual(deployment.status, DeploymentStatus.DEPLOYING)

    async def test_execute_deployment_marks_failed_on_activation_constraint_violation(self) -> None:
        engine = DeploymentEngine()
        session = SimpleNamespace(
            flush=AsyncMock(
                side_effect=[
                    None,
                    IntegrityError("stmt", {}, Exception("boom")),
                    None,
                ]
            ),
        )
        server = SimpleNamespace(id=7, name="srv-1")
        current_site = SimpleNamespace(
            id=2,
            domain="beta.example.com",
            config_template=SimpleNamespace(name="beta", caddyfile="respond beta", variables={}),
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        deployment = _FakeDeployment(site=current_site, server=server)

        with (
            patch("app.services.deployment_engine.site_repository.get_deployed_sites", new=AsyncMock(return_value=[])),
            patch("app.services.deployment_engine.caddy_service.adapt_caddyfile_to_json", new=AsyncMock(return_value={"apps": {}})),
            patch("app.services.deployment_engine.caddy_service.fetch_config", new=AsyncMock(return_value={})),
            patch("app.services.deployment_engine.caddy_service.extract_sites", return_value=set()),
            patch("app.services.deployment_engine.caddy_service.merge_managed_config", return_value={"apps": {}}),
            patch("app.services.deployment_engine.caddy_service.deploy_config", new=AsyncMock(return_value={"status": "ok"})),
            patch("app.services.deployment_engine.deployment_repository.get_active_deployment", new=AsyncMock(return_value=None)),
        ):
            result = await engine.execute_deployment(session, deployment, deployed_by="alice")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Deployment constraint violation for deployment 99")
        self.assertEqual(deployment.status, DeploymentStatus.FAILED)


if __name__ == "__main__":
    unittest.main()