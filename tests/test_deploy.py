"""Tests for the deployment shape: the port a host gives us, and the record a fresh container starts with.

Nothing here needs Docker, a server or a network. What it pins is the contract the
hosting depends on: the port the platform injects, the record being populated
before the first visitor arrives, and the deployment files naming routes the
application actually serves. None of those fail on a developer's machine, which is
exactly why they are asserted here rather than discovered on the day of a demo.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
from app import bootstrap, config
from app.store import Store

ROOT = Path(__file__).resolve().parent.parent


class PortTests(unittest.TestCase):
    """The port the platform asks for versus the one the project names."""

    def _settings(self, **environment: str) -> config.Settings:
        clean = {key: value for key, value in os.environ.items() if key not in ("PORT", "FORMUSENSE_PORT")}
        clean.update(environment)
        with mock.patch.dict(os.environ, clean, clear=True):
            return config.settings()

    def test_the_platform_port_is_followed_when_the_project_has_not_named_one(self) -> None:
        # Render, and most hosts like it, inject PORT and expect the service to bind
        # it. Before this, the container bound 8770 and the host found nothing there.
        self.assertEqual(self._settings(PORT="10000").port, 10000)

    def test_the_projects_own_port_wins_when_both_are_set(self) -> None:
        self.assertEqual(self._settings(PORT="10000", FORMUSENSE_PORT="9000").port, 9000)

    def test_the_documented_default_survives_an_environment_that_says_nothing(self) -> None:
        self.assertEqual(self._settings().port, 8770)

    def test_a_platform_port_that_is_not_a_usable_port_is_ignored(self) -> None:
        # Shells and tooling export PORT=0 out of habit. Following it would bind a
        # port the kernel picks, which nobody can then find; the default is better.
        for value in ("0", "-1", "70000", "", "not-a-number"):
            self.assertEqual(self._settings(PORT=value).port, 8770, repr(value))

    def test_a_typo_in_the_projects_own_port_is_not_swallowed(self) -> None:
        # Unlike the platform's variable, ours is only ever set on purpose, so a
        # value that is not a number is worth a loud failure rather than a silent
        # fall back to a port nobody asked for.
        with self.assertRaises(ValueError):
            self._settings(FORMUSENSE_PORT="eighty-eighty")


class StartupSeedTests(unittest.TestCase):
    """The record a fresh container has before the port opens."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-deploy-"))
        self.url = f"sqlite:///{(self.tmp / 'boot.db').as_posix()}"
        patcher = mock.patch.dict(os.environ, {"FORMUSENSE_DB_URL": self.url}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stored_products(self) -> int:
        store = Store(url=self.url)
        try:
            return len(store.products())
        finally:
            store.close()

    def test_an_empty_record_is_seeded_before_the_first_visitor(self) -> None:
        result = bootstrap.ensure_seeded()
        self.assertTrue(result["seeded"])
        self.assertGreater(result["products"], 0)
        self.assertEqual(self._stored_products(), result["products"])

    def test_a_populated_record_is_left_exactly_as_it_was(self) -> None:
        # The boot seed runs on every start, so it has to be a no-op the second time;
        # otherwise a restart would duplicate or wipe whatever the instance holds.
        first = bootstrap.ensure_seeded()
        second = bootstrap.ensure_seeded()
        self.assertFalse(second["seeded"])
        self.assertEqual(second["products"], first["products"])
        self.assertEqual(self._stored_products(), first["products"])


class LauncherTests(unittest.TestCase):
    """What `python run.py` does with no flags at all, which is how a host starts it."""

    def test_the_launcher_binds_where_the_environment_points_it(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key not in ("PORT", "FORMUSENSE_PORT")}
        environment.update({"PORT": "10000", "FORMUSENSE_HOST": "0.0.0.0"})
        with mock.patch.dict(os.environ, environment, clear=True):
            args = run.build_parser().parse_args([])
            self.assertEqual(args.port, 10000)
            self.assertEqual(args.host, "0.0.0.0")
            # An explicit flag still wins, so every documented local invocation is
            # unchanged by the host-facing defaults.
            overridden = run.build_parser().parse_args(["--host", "127.0.0.1", "--port", "8770"])
            self.assertEqual((overridden.host, overridden.port), ("127.0.0.1", 8770))

    def test_the_boot_seed_is_opt_in(self) -> None:
        for value in ("", "0", "false", "no", "off", "OFF", " False "):
            with mock.patch.dict(os.environ, {"FORMUSENSE_SEED_ON_START": value}, clear=False):
                self.assertFalse(run.seeds_on_start(), repr(value))
        for value in ("1", "true", "yes", "on"):
            with mock.patch.dict(os.environ, {"FORMUSENSE_SEED_ON_START": value}, clear=False):
                self.assertTrue(run.seeds_on_start(), repr(value))


class DeploymentFileTests(unittest.TestCase):
    """The files the host reads, checked against the application they describe."""

    def setUp(self) -> None:
        self.blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.server = (ROOT / "app" / "server.py").read_text(encoding="utf-8")

    def test_the_blueprint_describes_a_free_docker_web_service(self) -> None:
        for line in ("type: web", "runtime: docker", "plan: free", "name: formusense"):
            self.assertIn(line, self.blueprint)

    def test_the_health_check_the_blueprint_names_is_a_route_the_server_serves(self) -> None:
        # A health check pointed at a route that does not exist marks a working
        # deployment as failed, and the deployment file is the only place that says
        # which route it is.
        match = re.search(r"^\s*healthCheckPath:\s*(\S+)\s*$", self.blueprint, re.MULTILINE)
        self.assertIsNotNone(match, "render.yaml must declare healthCheckPath")
        route = match.group(1).strip("'\"")
        self.assertIn(f'_GET("{route}")', self.server)

    def test_the_blueprint_turns_the_boot_seed_on_and_binds_every_interface(self) -> None:
        self.assertIn("FORMUSENSE_SEED_ON_START", self.blueprint)
        self.assertIn("value: \"1\"", self.blueprint)
        self.assertIn("0.0.0.0", self.blueprint)

    def test_the_image_trains_the_model_with_the_settings_the_readme_quotes(self) -> None:
        # The published metrics come from a 20-variant dataset with seed 7. An image
        # that trains anything else reports different numbers than the README does.
        self.assertIn("--build-dataset", self.dockerfile)
        self.assertIn("--train", self.dockerfile)
        self.assertIn("--variants 20", self.dockerfile)
        self.assertIn("--dataset-seed 7", self.dockerfile)

    def test_the_image_declares_no_volume_over_the_artefacts_it_builds(self) -> None:
        # A VOLUME over app/data would shadow the model written by the build step,
        # which is the kind of failure that only shows up on a real host. Comments
        # are read past: the file talks about volumes in prose on purpose.
        instructions = [
            line.split()
            for line in self.dockerfile.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual([line for line in instructions if line[0].upper() == "VOLUME"], [])

    def test_the_image_does_not_pin_the_project_port_over_the_platforms(self) -> None:
        environment = self.dockerfile.split("WORKDIR")[0]
        self.assertIn("FORMUSENSE_HOST=0.0.0.0", environment)
        self.assertNotIn("FORMUSENSE_PORT", environment)


class KeepAliveTests(unittest.TestCase):
    """The ping that keeps a free instance warm has to knock on the right door."""

    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "keepalive.yml").read_text(encoding="utf-8")
        self.server = (ROOT / "app" / "server.py").read_text(encoding="utf-8")

    def test_the_ping_targets_the_address_the_readme_advertises(self) -> None:
        # Two places quote the live URL; letting them drift means the ping wakes one
        # address while a judge opens another.
        match = re.search(r"^\s*APP_URL:\s*(\S+)\s*$", self.workflow, re.MULTILINE)
        self.assertIsNotNone(match, "keepalive.yml must declare APP_URL")
        url = match.group(1).rstrip("/")
        self.assertTrue(url.startswith("https://"), url)
        self.assertIn(url, (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_the_ping_uses_an_always_open_route(self) -> None:
        self.assertIn("/api/health", self.workflow)
        self.assertIn('_GET("/api/health")', self.server)


if __name__ == "__main__":
    unittest.main()
