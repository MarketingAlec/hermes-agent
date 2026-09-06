"""Fail-closed source admission for the release-pinned prototype."""

from __future__ import annotations

import hashlib
import pkgutil
import sys
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Callable, Mapping


EXPECTED_HASHES: Mapping[str, str] = {
    # Release sources reached by seed initialization and the fixed first-party
    # claim hook, plus retained conservative static pins.
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
    "agent.reasoning_effort": "9296b9f616a296c9111257f07723d1f8050843f5a3d7bc3d3909f3a8a2ac0e0e",
    "agent.redact": "db98c5b9cf5727abbb471b5009d19d3f8230863869cde207d53c5e5d8d4831f0",
    "agent.relay_runtime": "974612d5aaa539f2abbe616a99182266369ce7a7f2db7b4b3fb81438592c30a2",
    "agent.secret_scope": "51d91d280ef05d59c0c450dbfaecd6f50b314a4ff75614cb23b5288a308c645d",
    "agent.session_activity": "4e48691002d499b6a8ef7927065178ca4bf64fd7beca4ac8e7123e4dd22a8660",
    "agent.skill_commands": "3ff2f67ee417be29e90994742b7d3561de427241093056cc307c6dfafebc7b64",
    "agent.skill_preprocessing": "d730dfef24cbc4e2791fe0131d8e1221ba3cf7345e0842e8dcceec286360e16f",
    "agent.skill_utils": "2c9898d5eca5725b95cd40834663bdfbadc21898e92c2f5054e74a027d5d9bc4",
    "agent.transports": "fe832003b28c5470608eb51a055e5cc759f1fb81d93174b54dbbd217f97bbffe",
    "agent.transports.base": "e9b0d020f9cf1aae5d0a470363c7694cb3f3f0257fbeff00fc80435e9b80befe",
    "agent.transports.codex": "c90e07e6bbb01e27fe2ebd24ca96f6efffd0458738efb83fe064851b993a05c3",
    "agent.transports.types": "148b1f6389d10952a70da2a79c7a6baf056f7a41178b9b5e0b673626dad2fa2b",
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
    "hermes_cli.managed_scope": "6b542220964ddaebc176ddbccb833174ab309cde97e0e2ba7a309c7b98ce26f9",
    "hermes_cli.middleware": "cae867efa6a5b3c80675f9d7571fb56cea7519a44364bfc9c938c3e5c6f4d5f6",
    "hermes_cli.nous_account": "d13b2f84d7a5564b3c22d85841212e13855ea13b293ee087bc1d91c19e9b0182",
    "hermes_cli.nous_subscription": "8276f2ffb4b220554f77ccfabd89079114ab1183bd2b0c88ce43e153f8cd1fbc",
    "hermes_cli.observability": "5d8736f56d865fe90993ac2907496f64ae16e218b7104b95f36e810bd5a8207c",
    "hermes_cli.observability.relay_shared_metrics": "6b0b415dd7eb291a2e46d5b6da4956b4e9d73945cdea4de2d610fa054ad9d691",
    "hermes_cli.observability.shared_metrics": "1ea2f6d95d80217a4e7c999e203b572c0fc27d16f1fa82fc3304f28748d66389",
    "hermes_cli.observability.shared_metrics_contract": "27baae9d80697fb795c43f173ceb8bb3dc545602ff1d608a446b210004c87495",
    "hermes_cli.observability.shared_metrics_subscriber": "90c0fd4ecca7d0f44d30a48dad99b391040e94627464a3c9e7e9d04d03afdbbf",
    "hermes_cli.personality": "520cf5dbcda99247e39fecf520eac897d270e28dd26908a5c1686131a0d5f6b0",
    "hermes_cli.platforms": "a04bd017cacbf9959bba396f94c5e1e9d7df77d88aaef92e858e89b9705d4b5a",
    "hermes_cli.plugin_capabilities": "cafbd3ffb16cb233e48fca599cce1002b7810f5cfe18a4c09d59b1acccf7dd0c",
    "hermes_cli.plugins": "63cef01ae81a23c859170d36860c9ac257f320266a88640a8b9690952728bba6",
    "hermes_cli.profiles": "edeafa558cea28cc42ce4e80a21489a3176454a48bae9f767d8c179b954e0981",
    "hermes_cli.relay_plugin_cutover": "bdd4fe4e20f2f5d053af50bac27e7395b81a56cc9a90bc172e6d31a82b33b971",
    "hermes_cli.route_identity": "5d788c2af30ad65bc69e55dabf1e76f97c6e370e9024bfe1e4897ddd95578363",
    "hermes_cli.secret_prompt": "2c999150615d3fc03b5bcc4cf0ec6ef17eeb84c2cbcbf9d19a1ca9ad938955ff",
    "hermes_cli.sqlite_runtime": "682ae7f6a783673b7687377a37f210f5b1f86f54d227baed95df279bdd561310",
    "hermes_cli.sqlite_safe_read": "119a8435496d6136cdb7267a4180158308590ee129a16a1cedd39932323d03cb",
    "hermes_cli.sqlite_util": "55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438",
    "hermes_cli.tools_config": "f6edc900cbb16ada9313d004820ae054be84428b1c3bab729555c50a4499bb65",
    "hermes_cli.toolset_scope": "22180e76ae344684a24dcaac4695055eaeb7ec34afe2cc1edff90589e97f3aee",
    "hermes_cli.urllib_security": "dbeb3be6d995dcc2668cf60f268aa83ca9f76c0bffa31e1355174d3b0b3cee70",
    "hermes_constants": "b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068",
    "hermes_state": "696fd2d37d66a599228f5b1be5d5b76423b744149c83a2fbd59bf3f45a5f3891",
    "hermes_state_common": "fa07edf0fc394c7a28c68165dd45b974d21dfa2a0da7c3416d6d7ba85fbf2799",
    "hermes_state_portability": "f7bfb93d0c3fb16f53d0c148aa9991b755e8debe3fd7e6829b658c731a1f379e",
    "hermes_state_schema": "4093651a0861a6c6108ccca39db3453317a27720bb1374967022ef653ef2436d",
    "hermes_state_search": "cc7585afd1397240ce82ddcaec7d43a0b21e4871659ff8a50a386dccb582e41d",
    "plugins.model_providers.actual": "ae17a0d6a09f5b583161646a72278b49343c1c6c6f73a1336d895c298a378472",
    "plugins.model_providers.ai_gateway": "e2990bb8160b18c48f13da64f25af04d88b6dfba69c5f638069cf0638d732790",
    "plugins.model_providers.alibaba": "6e1fc518db2a95d723c33e9968786d38e01b075e70daa338b45f34c3ae41613d",
    "plugins.model_providers.alibaba_coding_plan": "6839d6a1ea8c52a80709486152f601f6b75e558ef2c011a5efd356942c96c937",
    "plugins.model_providers.anthropic": "5890d1b6591a52a88578f66aa8368c66f084efd348518e798d825cf85654967d",
    "plugins.model_providers.arcee": "33cec454165691796b99ce1b89c022d7097344e339224a25c2893f0470cf25ec",
    "plugins.model_providers.azure_foundry": "e80db41b65d03c47c938b07f78c2f0013f92c8e543e33e5a87063a3b91cde7df",
    "plugins.model_providers.bedrock": "07771401db7529e6b6d598e2b15dc158ca42a09c876bb163046c4c9b51771dd8",
    "plugins.model_providers.commandcode": "3e22f947b915b4cdb8b88c782252afca578a72a593c3fb64473132ee91f5c9da",
    "plugins.model_providers.copilot": "e0bddb20e9647dbf6a658dde6128245d469625e86822a06eeee1d1472ce9bba9",
    "plugins.model_providers.copilot_acp": "131272603202c2fd03bba15571e57ab5d5cf716459616509066e864433b5b17b",
    "plugins.model_providers.custom": "c91b234c54c5eb1b46858ee385e6b228cfe5120851e998961edde9fc16e51efe",
    "plugins.model_providers.deepinfra": "7ec889ec71fe0c1f40d87e88b2563ee37f60628ad7903785abefa8b3ec90c646",
    "plugins.model_providers.deepseek": "0a67eef0f9961db4420ca034470a345e6beb32aa248756c54f587a808aaff8c0",
    "plugins.model_providers.fireworks": "d862420591a292e2981185bd9b500af4c15cd767483150c0bf1b6dfdb2cf0f30",
    "plugins.model_providers.gemini": "45ed416ca66e21fa6a57db0da4e6ed90b15f2383033eb72a9e72b168bd0c602d",
    "plugins.model_providers.gmi": "34ec2ebf7370b3b0e8e9744f663abac0cf18f5eef501f941ca5d709f368d5567",
    "plugins.model_providers.huggingface": "988766ef147dcae4d8002b748e14fe5f12a32d9a0525d5d1b10dd0f93edb48f8",
    "plugins.model_providers.kilocode": "b9656f37ae34a227c5aa201c3c545265b59ec4a73303821696c895ef682eb257",
    "plugins.model_providers.kimi_coding": "267bf7f336bec341a0a1794a2dfe17732de85b6db212c1dcbbf39f0897c55a40",
    "plugins.model_providers.meta_ai": "ce97a86cca98ee3542eddc05a1cb681837f8c7832220311b33a93693758f387e",
    "plugins.model_providers.minimax": "95f6cc38f95db5ccc48661adfa1522eb147e0a9bf754bc6fcdb422b43f0d0de1",
    "plugins.model_providers.nebius_token_factory": "e823bbcff4b17884b15a47564ed27eff467ea6b48025a820273ab5d5dd30d601",
    "plugins.model_providers.nous": "edc99481115f06d1dc7f0a2a24d7ddda0ecad230f4c211740547443db20f17b7",
    "plugins.model_providers.novita": "cdd636b410015a0a186648b48d0ebe377d85cc3613a356208c1e28e258996927",
    "plugins.model_providers.nvidia": "1ff022c55b767d8a3e002dfe79920e854889c39654ce2c2ad2d24fe3613e9fae",
    "plugins.model_providers.ollama_cloud": "02802cd20f0fbb775eaa827cfe46ce242dbd6222c74d39cfd5903990b9c65f0b",
    "plugins.model_providers.openai_codex": "e07f3303b21752815fa8908821f4c39a431a8dc9aeb6d277b1b4958dd14797c4",
    "plugins.model_providers.opencode_free": "8538991c120acda714d38da711354d1106f0e2c5bc5cbbe084252d10ab2174c6",
    "plugins.model_providers.opencode_zen": "72c34089de033335afb02e3d47d50bb9516053894908b9f6d942f5bf39c9763b",
    "plugins.model_providers.openrouter": "a1aba83d979418c353010f851f84dfdc7fd9855a1d9b6a92bb416850552e89c9",
    "plugins.model_providers.qwen_oauth": "e0deac050e59f3f592b2f1ab18b9625e46c7948b0b9f57b795e6bd3f5706740d",
    "plugins.model_providers.router": "2f369b00fccb6f9377f829e27d1665b7582cea4d1440d119a9444db9f89b73e2",
    "plugins.model_providers.stepfun": "3b63413407f6e40d03ac71a52590c240e863c92f3627583041c148155458f9ae",
    "plugins.model_providers.upstage": "20bb7e742767554a115bb6d7f025feaa1112dfd387a5704ee24e099516348078",
    "plugins.model_providers.vertex": "9d5ee5709bb36ca101461f8ddf4b9a1b607c35235a898a9b567856b2387c5b7f",
    "plugins.model_providers.xai": "71aaf875905ce9d0b8cf261be4b8aab1323b9e900135b4d9771ba789d9656472",
    "plugins.model_providers.xiaomi": "1aae5cf24f75eb1a2a9b9f1e9916cc2416becc8613271aef31dccb0e543011f7",
    "plugins.model_providers.zai": "792f699b0f5cf09e07a3433779957d9f793bf35b4099eaaa8b0f86f31ccfb212",
    "providers": "eb55ccb80726aa2d4c8f1294a4ca8fdab82fc6f34b8c374b423b821d8012980b",
    "providers.base": "65c735f999f60011c475db33ce23012de6ac5cf40d8bb255f0021867ebbb23fe",
    "registration_lifecycle": "42c7a95159a02cd5dd252a5f5d43e81d083656b3498b4b95bc09b078e3fbdee0",
    "tools": "f20d844ea0b00d501fe11de2f44768277257d0fa929bda2c39d7c08cc445a4c7",
    "tools.managed_tool_gateway": "b5a822bb80b997de4325a0c81e3b643135c055b749cf107ee14afbdb0e07c9c8",
    "tools.registry": "f95d011cae75f0426dd77cb1e25df6bebb61a067d795312df297cb877dd616f5",
    "tools.todo_tool": "d9d9ec131bc4cf76a6d763b13a394003fc7638cb3c271b3635bcef439c4566c9",
    "tools.tool_backend_helpers": "764d1319d008b154f4b3182712efcdcbebb3e32344a8507ed87dfd10dd75d0eb",
    "toolsets": "31752adbf72647367b1ba31848f8be06001df706d12d1fdcd2fabd38f284de85",
    "utils": "300411980eed8013b69a13e8fbc6bbc908685f7718a015f2b7126e37a3ce89a5",
}

SPECIAL_SOURCE_PATHS: Mapping[str, Path] = {
    "agent": Path("agent/__init__.py"),
    "agent.transports": Path("agent/transports/__init__.py"),
    "gateway": Path("gateway/__init__.py"),
    "hermes_cli": Path("hermes_cli/__init__.py"),
    "hermes_cli.observability": Path("hermes_cli/observability/__init__.py"),
    "plugins.model_providers.actual": Path(
        "plugins/model-providers/actual/__init__.py"
    ),
    "plugins.model_providers.ai_gateway": Path(
        "plugins/model-providers/ai-gateway/__init__.py"
    ),
    "plugins.model_providers.alibaba": Path(
        "plugins/model-providers/alibaba/__init__.py"
    ),
    "plugins.model_providers.alibaba_coding_plan": Path(
        "plugins/model-providers/alibaba-coding-plan/__init__.py"
    ),
    "plugins.model_providers.anthropic": Path(
        "plugins/model-providers/anthropic/__init__.py"
    ),
    "plugins.model_providers.arcee": Path("plugins/model-providers/arcee/__init__.py"),
    "plugins.model_providers.azure_foundry": Path(
        "plugins/model-providers/azure-foundry/__init__.py"
    ),
    "plugins.model_providers.bedrock": Path(
        "plugins/model-providers/bedrock/__init__.py"
    ),
    "plugins.model_providers.commandcode": Path(
        "plugins/model-providers/commandcode/__init__.py"
    ),
    "plugins.model_providers.copilot": Path(
        "plugins/model-providers/copilot/__init__.py"
    ),
    "plugins.model_providers.copilot_acp": Path(
        "plugins/model-providers/copilot-acp/__init__.py"
    ),
    "plugins.model_providers.custom": Path(
        "plugins/model-providers/custom/__init__.py"
    ),
    "plugins.model_providers.deepinfra": Path(
        "plugins/model-providers/deepinfra/__init__.py"
    ),
    "plugins.model_providers.deepseek": Path(
        "plugins/model-providers/deepseek/__init__.py"
    ),
    "plugins.model_providers.fireworks": Path(
        "plugins/model-providers/fireworks/__init__.py"
    ),
    "plugins.model_providers.gemini": Path(
        "plugins/model-providers/gemini/__init__.py"
    ),
    "plugins.model_providers.gmi": Path("plugins/model-providers/gmi/__init__.py"),
    "plugins.model_providers.huggingface": Path(
        "plugins/model-providers/huggingface/__init__.py"
    ),
    "plugins.model_providers.kilocode": Path(
        "plugins/model-providers/kilocode/__init__.py"
    ),
    "plugins.model_providers.kimi_coding": Path(
        "plugins/model-providers/kimi-coding/__init__.py"
    ),
    "plugins.model_providers.meta_ai": Path(
        "plugins/model-providers/meta-ai/__init__.py"
    ),
    "plugins.model_providers.minimax": Path(
        "plugins/model-providers/minimax/__init__.py"
    ),
    "plugins.model_providers.nebius_token_factory": Path(
        "plugins/model-providers/nebius-token-factory/__init__.py"
    ),
    "plugins.model_providers.nous": Path("plugins/model-providers/nous/__init__.py"),
    "plugins.model_providers.novita": Path(
        "plugins/model-providers/novita/__init__.py"
    ),
    "plugins.model_providers.nvidia": Path(
        "plugins/model-providers/nvidia/__init__.py"
    ),
    "plugins.model_providers.ollama_cloud": Path(
        "plugins/model-providers/ollama-cloud/__init__.py"
    ),
    "plugins.model_providers.openai_codex": Path(
        "plugins/model-providers/openai-codex/__init__.py"
    ),
    "plugins.model_providers.opencode_free": Path(
        "plugins/model-providers/opencode-free/__init__.py"
    ),
    "plugins.model_providers.opencode_zen": Path(
        "plugins/model-providers/opencode-zen/__init__.py"
    ),
    "plugins.model_providers.openrouter": Path(
        "plugins/model-providers/openrouter/__init__.py"
    ),
    "plugins.model_providers.qwen_oauth": Path(
        "plugins/model-providers/qwen-oauth/__init__.py"
    ),
    "plugins.model_providers.router": Path(
        "plugins/model-providers/router/__init__.py"
    ),
    "plugins.model_providers.stepfun": Path(
        "plugins/model-providers/stepfun/__init__.py"
    ),
    "plugins.model_providers.upstage": Path(
        "plugins/model-providers/upstage/__init__.py"
    ),
    "plugins.model_providers.vertex": Path(
        "plugins/model-providers/vertex/__init__.py"
    ),
    "plugins.model_providers.xai": Path("plugins/model-providers/xai/__init__.py"),
    "plugins.model_providers.xiaomi": Path(
        "plugins/model-providers/xiaomi/__init__.py"
    ),
    "plugins.model_providers.zai": Path("plugins/model-providers/zai/__init__.py"),
    "providers": Path("providers/__init__.py"),
    "tools": Path("tools/__init__.py"),
}

SOURCE_PATHS: Mapping[str, Path] = {
    module_name: SPECIAL_SOURCE_PATHS.get(
        module_name, Path(*module_name.split(".")).with_suffix(".py")
    )
    for module_name in EXPECTED_HASHES
}

BUNDLED_PROVIDER_PREFIX = "plugins.model_providers."
EXPECTED_BUNDLED_PROVIDER_SOURCES = frozenset(
    source_path
    for module_name, source_path in SOURCE_PATHS.items()
    if module_name.startswith(BUNDLED_PROVIDER_PREFIX)
)
EXPECTED_LEGACY_PROVIDER_MODULES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Admission:
    ok: bool
    error: str | None
    origins: Mapping[str, Path]


def _module_origin(module_name: str) -> Path:
    """Resolve the source Python would use without importing package parents."""
    loaded_module = sys.modules.get(module_name)
    if loaded_module is not None:
        spec = getattr(loaded_module, "__spec__", None)
        loaded_file = getattr(loaded_module, "__file__", None)
        if spec is None or spec.origin is None or loaded_file is None:
            raise LookupError(module_name)
        origin = Path(spec.origin).expanduser().resolve()
        if Path(loaded_file).expanduser().resolve() != origin:
            raise RuntimeError(f"loaded source origin mismatch: {module_name}")
        return origin

    if module_name.startswith(BUNDLED_PROVIDER_PREFIX):
        providers_origin = _module_origin("providers")
        providers_root = providers_origin.parent.parent
        origin = (providers_root / SOURCE_PATHS[module_name]).resolve()
        if not origin.is_file():
            raise LookupError(module_name)
        return origin

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


def _provider_membership_error(source_root: Path) -> str | None:
    """Check the two fixed release directories provider discovery executes."""
    bundled_dir = source_root / "plugins" / "model-providers"
    try:
        bundled_sources = frozenset(
            (child / "__init__.py").relative_to(source_root)
            for child in bundled_dir.iterdir()
            if child.is_dir()
            and not child.name.startswith(("_", "."))
            and (child / "__init__.py").is_file()
        )
    except OSError as exc:
        return f"bundled provider source membership unavailable: {exc}"
    if bundled_sources != EXPECTED_BUNDLED_PROVIDER_SOURCES:
        return "bundled provider source membership mismatch"

    legacy_dir = source_root / "providers"
    try:
        legacy_modules = frozenset(
            module_name
            for _finder, module_name, _is_package in pkgutil.iter_modules([
                str(legacy_dir)
            ])
            if not module_name.startswith("_") and module_name != "base"
        )
    except OSError as exc:
        return f"legacy provider module membership unavailable: {exc}"
    if legacy_modules != EXPECTED_LEGACY_PROVIDER_MODULES:
        return "legacy provider module membership mismatch"
    return None


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
    if coherent_root is None:
        return Admission(False, "source inventory is empty", origins)
    membership_error = _provider_membership_error(coherent_root)
    if membership_error is not None:
        return Admission(False, membership_error, origins)
    return Admission(True, None, origins)
