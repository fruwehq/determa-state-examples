from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import determa.state as ds


@dataclass(frozen=True)
class DefinitionRegistry:
    bundles: dict[int, ds.Bundle]
    resolver: ds.MemoryArtifactResolver
    routes: dict[tuple[str, str], tuple[str, ...]]

    @classmethod
    def load(cls) -> DefinitionRegistry:
        package = files("order_service")
        bundles = {
            version: ds.load_bundle(
                package.joinpath("machines", f"order-v{version}.yaml").read_text()
            )
            for version in (1, 2)
        }
        descriptor: dict[str, Any] = json.loads(
            package.joinpath("migrations", "order-v1-to-v2.json").read_text()
        )
        digest = str(descriptor["migration_descriptor_digest"])
        resolver = ds.MemoryArtifactResolver(
            definitions={bundle.fingerprint: bundle for bundle in bundles.values()},
            migration_descriptors={digest: descriptor},
        )
        return cls(
            bundles=bundles,
            resolver=resolver,
            routes={(bundles[1].fingerprint, bundles[2].fingerprint): (digest,)},
        )

    def bundle(self, version: int) -> ds.Bundle:
        try:
            return self.bundles[version]
        except KeyError as exc:
            raise ValueError(f"unsupported definition version: {version}") from exc

    def route(self, source: str, target: str) -> tuple[str, ...]:
        if source == target:
            return ()
        try:
            return self.routes[(source, target)]
        except KeyError as exc:
            raise ValueError("no trusted migration route") from exc
