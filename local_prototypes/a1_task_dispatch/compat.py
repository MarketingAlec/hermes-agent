"""Fail-closed source admission for the release-pinned prototype."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Callable, Mapping


EXPECTED_HASHES: Mapping[str, str] = {
    # Release-fixed import-time closure of the 16 adapter/helper seeds.
    "agent": "99f85bec3844de2bfbd984d64768b22a3a4c5dba01375c6832b292d24e1617e4",
    "agent.auxiliary_client": "de28c68a86491988e8098f9fd021e99c95e866f3782ba48893f561d33f4f24a3",
    "agent.codex_headers": "a0a83440762b8141240521fe30ee41811c162ff4a39906b3c96c0b187a2d9b63",
    "agent.context_compressor": "13a944ae4c20ce0c9c6f920b45dc7b262e45289d0f4c4ff4ef0a8e509d0ec062",
    "agent.context_engine": "5ee0a74e81b0bba0075c73df161c9b02a78659768241ff65c9488e46a3428b1d",
    "agent.conversation_compression": "981aa8113cdbbe130a86ed81f12c95e5ce2442d7e571f00f7936ad8b3237d39e",
    "agent.credential_persistence": "0e403ab04fd79b4da2415e73a7d1f5873ad575c7bc69c30fd117b76fe1781ce6",
    "agent.credential_pool": "0936a509bb894915bd43bcd0df47140a5e4761e91f3cd6f16d8c936d2f7aa772",
    "agent.delegation_context": "d185d1746efa5516a2697dc7e58b1e049300239300475766e91decbfc0c2f754",
    "agent.error_classifier": "7efe8663b2c22b3c709c84b83b06ae5dd8e9511f17d9c824f8724050ec2363f8",
    "agent.file_safety": "f2cb782cda3f091acba507e3af6a31e7326770dfc6b75657cd5adb1006521db9",
    "agent.iteration_budget": "d0d5b192b6cf555678a5507937a32f043cec8523250fec0fa26b09457f895047",
    "agent.jiter_preload": "1df1336c3c6bc67904dcc13572c159d169afa71ad94494dc7ddebe8843337536",
    "agent.memory_manager": "a706285f0bbad1cbca57c2c1cf5151ec3fda04e586210333eb6639966b956639",
    "agent.memory_provider": "902e061d33b372cc28facd8c7bf7fe032aacda3f17ea1e4107fea9fd01885335",
    "agent.message_metadata": "8dbb47726075348eb4ef6a184d52885cb39bf2366e240966c4d07ab82ac41d17",
    "agent.message_sanitization": "8a595fa5a2b8faf6080430840255404f638d932bed23d51af3ce5a99fe02cc54",
    "agent.model_metadata": "0d092629ebf8134bab554ccc71c14f425846b983d46257ae337f5a98eba978b3",
    "agent.portal_tags": "26f737718acefe25b061aa5cac33413bf948134b6a6ee14d03e81a54fae03d76",
    "agent.prompt_cache_boundary": "4ec97a0da939ae17b322602c7eb24b09b1194cfbee41e7c2fcad487cd60f98d6",
    "agent.redact": "db98c5b9cf5727abbb471b5009d19d3f8230863869cde207d53c5e5d8d4831f0",
    "agent.secret_scope": "51d91d280ef05d59c0c450dbfaecd6f50b314a4ff75614cb23b5288a308c645d",
    "agent.session_activity": "4e48691002d499b6a8ef7927065178ca4bf64fd7beca4ac8e7123e4dd22a8660",
    "agent.skill_commands": "3ff2f67ee417be29e90994742b7d3561de427241093056cc307c6dfafebc7b64",
    "agent.skill_preprocessing": "d730dfef24cbc4e2791fe0131d8e1221ba3cf7345e0842e8dcceec286360e16f",
    "agent.skill_utils": "2c9898d5eca5725b95cd40834663bdfbadc21898e92c2f5054e74a027d5d9bc4",
    "agent.turn_context": "86a4f6c61183167850046b025a8f5c7b8601fb910f8a9ee464535afccc541c06",
    "gateway": "7ecb71487af8805400cb7c9163cd1f7c8ebb9b699d8a695dac3a0ced9ff38fa7",
    "gateway.config": "658b71126dcc53c36914d16abb05a249acb82c8f5d6bd54385053afa9c172fd1",
    "gateway.dead_targets": "3a678ef2eea5a6c08a04bf3e88924466f692000b4ab002d2d1392632f7709532",
    "gateway.delivery": "8bf44b147db0c21d304e7b65cd05d4c86a27bf01a7b5303b2e8e6b72be4e8efb",
    "gateway.lifecycle_ledger": "85b7952df35aecf5cc46093019d3e4042e18de28c3fb00eb982ad1b69e49d1c0",
    "gateway.memory_status": "d63fac1fe71761808f95cd1373dacac288a571bc57593a08ef33de783b007f02",
    "gateway.platform_registry": "5c3f0758076d35d9f6cffa2664c52d0ae3de68cea4c59595054a8ba4a4cc3bce",
    "gateway.restart": "18e1aeb0b27210c6bb9dc9ff34c529d93c0e4d57a7d588ea8e58ee8bea92b308",
    "gateway.session": "a6928641990bd0531b29c1de3a36c760e95528c52ba5cde6aefdd9dfb32634ff",
    "gateway.session_context": "b68908ffec7891b12080933a4d50a63d449479a596335c2579776c6e545c4500",
    "gateway.shutdown_watchdog": "fac4bd70b71cb640d6702765d20946dfeaa7f6701440dc0cc4cc72b72dc4241a",
    "gateway.whatsapp_identity": "70e1d891ae68468d9f10314f362a66541a97c9ac6ff277b6368d51ac1d21c8e4",
    "hermes_cli": "fd84470016147d1ed16b8655a60fd5e5c118c26a1a9816d1276933d32c337ff5",
    "hermes_cli._subprocess_compat": "9d96bab89d318ce1adeddfe9008889dd3d721e19be59b0bc9bec4387570a75ad",
    "hermes_cli.archive_safe": "53964e556d69ca8ebbaaf7d759da2c0143f8a5b663909bee7d5b0bd4b33d70ec",
    "hermes_cli.auth": "98d96872425c1e4564fe6a5cc27731166266b1b6795d0187db1d42cf1c92b84e",
    "hermes_cli.cli_output": "d7303fc721dc2f4f08c3f4ba59169537aab7ecc1bbbc8e1aec480154621304af",
    "hermes_cli.colors": "82a0e2b86010406ab20f04d0950b8d91953dee6dd6d86c834172dcc5314e35ff",
    "hermes_cli.config": "cbce38a2aeed3838dc768d69ee0b31278a211f64bfec66bf786f571c775b3730",
    "hermes_cli.config_defaults": "2a89363e62dac1695ccfdc42b488e9eafd94edb104dcceb24bc1d9f9e626eb64",
    "hermes_cli.default_soul": "a7581dbec782fc22893e76153bc56da6144ac3abe9b74b9308ec5c324721f46e",
    "hermes_cli.kanban": "8e4e51a1403171e706aea60b703eb141e7909615d845efdbc101dfd8cdc6bca0",
    "hermes_cli.kanban_db": "4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0",
    "hermes_cli.kanban_swarm": "2bd90493e8b195bf1724c244a16b2b429a9586c3b28f008dcbb86a4bb20a69ff",
    "hermes_cli.lifecycle": "e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd",
    "hermes_cli.nous_account": "d13b2f84d7a5564b3c22d85841212e13855ea13b293ee087bc1d91c19e9b0182",
    "hermes_cli.nous_subscription": "8276f2ffb4b220554f77ccfabd89079114ab1183bd2b0c88ce43e153f8cd1fbc",
    "hermes_cli.personality": "520cf5dbcda99247e39fecf520eac897d270e28dd26908a5c1686131a0d5f6b0",
    "hermes_cli.platforms": "a04bd017cacbf9959bba396f94c5e1e9d7df77d88aaef92e858e89b9705d4b5a",
    "hermes_cli.profiles": "edeafa558cea28cc42ce4e80a21489a3176454a48bae9f767d8c179b954e0981",
    "hermes_cli.route_identity": "5d788c2af30ad65bc69e55dabf1e76f97c6e370e9024bfe1e4897ddd95578363",
    "hermes_cli.secret_prompt": "2c999150615d3fc03b5bcc4cf0ec6ef17eeb84c2cbcbf9d19a1ca9ad938955ff",
    "hermes_cli.sqlite_runtime": "682ae7f6a783673b7687377a37f210f5b1f86f54d227baed95df279bdd561310",
    "hermes_cli.sqlite_safe_read": "119a8435496d6136cdb7267a4180158308590ee129a16a1cedd39932323d03cb",
    "hermes_cli.sqlite_util": "55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438",
    "hermes_cli.tools_config": "f6edc900cbb16ada9313d004820ae054be84428b1c3bab729555c50a4499bb65",
    "hermes_cli.toolset_scope": "22180e76ae344684a24dcaac4695055eaeb7ec34afe2cc1edff90589e97f3aee",
    "hermes_constants": "b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068",
    "hermes_state": "696fd2d37d66a599228f5b1be5d5b76423b744149c83a2fbd59bf3f45a5f3891",
    "hermes_state_common": "fa07edf0fc394c7a28c68165dd45b974d21dfa2a0da7c3416d6d7ba85fbf2799",
    "hermes_state_portability": "f7bfb93d0c3fb16f53d0c148aa9991b755e8debe3fd7e6829b658c731a1f379e",
    "hermes_state_schema": "4093651a0861a6c6108ccca39db3453317a27720bb1374967022ef653ef2436d",
    "hermes_state_search": "cc7585afd1397240ce82ddcaec7d43a0b21e4871659ff8a50a386dccb582e41d",
    "providers": "eb55ccb80726aa2d4c8f1294a4ca8fdab82fc6f34b8c374b423b821d8012980b",
    "providers.base": "65c735f999f60011c475db33ce23012de6ac5cf40d8bb255f0021867ebbb23fe",
    "tools": "f20d844ea0b00d501fe11de2f44768277257d0fa929bda2c39d7c08cc445a4c7",
    "tools.managed_tool_gateway": "b5a822bb80b997de4325a0c81e3b643135c055b749cf107ee14afbdb0e07c9c8",
    "tools.registry": "f95d011cae75f0426dd77cb1e25df6bebb61a067d795312df297cb877dd616f5",
    "tools.todo_tool": "d9d9ec131bc4cf76a6d763b13a394003fc7638cb3c271b3635bcef439c4566c9",
    "tools.tool_backend_helpers": "764d1319d008b154f4b3182712efcdcbebb3e32344a8507ed87dfd10dd75d0eb",
    "toolsets": "31752adbf72647367b1ba31848f8be06001df706d12d1fdcd2fabd38f284de85",
    "utils": "300411980eed8013b69a13e8fbc6bbc908685f7718a015f2b7126e37a3ce89a5",
}

PACKAGE_SOURCE_PATHS: Mapping[str, Path] = {
    "agent": Path("agent/__init__.py"),
    "gateway": Path("gateway/__init__.py"),
    "hermes_cli": Path("hermes_cli/__init__.py"),
    "providers": Path("providers/__init__.py"),
    "tools": Path("tools/__init__.py"),
}

SOURCE_PATHS: Mapping[str, Path] = {
    module_name: PACKAGE_SOURCE_PATHS.get(
        module_name, Path(*module_name.split(".")).with_suffix(".py")
    )
    for module_name in EXPECTED_HASHES
}


@dataclass(frozen=True)
class Admission:
    ok: bool
    error: str | None
    origins: Mapping[str, Path]


def _module_origin(module_name: str) -> Path:
    """Resolve the source Python would use without importing package parents."""
    search_path = None
    parts = module_name.split(".")
    for index in range(len(parts)):
        qualified = ".".join(parts[: index + 1])
        loaded = sys.modules.get(qualified)
        if loaded is not None:
            spec = getattr(loaded, "__spec__", None)
            loaded_file = getattr(loaded, "__file__", None)
            if spec is None:
                raise LookupError(qualified)
        else:
            spec = PathFinder.find_spec(qualified, search_path)
            loaded_file = None
        if spec is None:
            raise LookupError(qualified)
        if index == len(parts) - 1:
            if spec.origin is None:
                raise LookupError(module_name)
            origin = Path(spec.origin).expanduser().resolve()
            if (
                loaded_file is not None
                and Path(loaded_file).expanduser().resolve() != origin
            ):
                raise RuntimeError(f"loaded source origin mismatch: {module_name}")
            return origin
        locations = (
            getattr(loaded, "__path__", None)
            if loaded is not None
            else spec.submodule_search_locations
        )
        if locations is None:
            raise LookupError(qualified)
        search_path = list(locations)
    raise LookupError(module_name)


def _source_root(module_name: str, origin: Path) -> Path:
    relative = SOURCE_PATHS[module_name]
    root = origin
    for _part in relative.parts:
        root = root.parent
    if (root / relative).resolve() != origin:
        raise RuntimeError(f"source origin mismatch: {module_name}")
    return root


def check_compatibility(
    *,
    origin_resolver: Callable[[str], Path] = _module_origin,
) -> Admission:
    """Verify every load-bearing private module without importing it."""
    origins: dict[str, Path] = {}
    coherent_root: Path | None = None
    for module_name, expected_hash in EXPECTED_HASHES.items():
        try:
            origin = Path(origin_resolver(module_name)).expanduser().resolve()
            digest = hashlib.sha256(origin.read_bytes()).hexdigest()
        except Exception as exc:
            return Admission(
                False, f"source unavailable: {module_name}: {exc}", origins
            )
        origins[module_name] = origin
        if digest != expected_hash:
            return Admission(False, f"source hash mismatch: {module_name}", origins)
        try:
            root = _source_root(module_name, origin)
        except Exception:
            return Admission(False, f"source origin mismatch: {module_name}", origins)
        if coherent_root is None:
            coherent_root = root
        elif root != coherent_root:
            return Admission(False, f"source origin mismatch: {module_name}", origins)
    return Admission(True, None, origins)
