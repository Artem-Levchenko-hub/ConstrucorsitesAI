"""Execute the server hook from an old tree refreshed with the managed kit."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from tests.test_max_project_kit import _config
from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES, render_max_managed_files

HARNESS = r"""
const fs=require('node:fs');const vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module');
const input=JSON.parse(fs.readFileSync(0,'utf8'));const sent=[];
const ctx={exports:{},Buffer,Date,AbortSignal,setTimeout,
process:{env:{OMNIA_PROJECT_ID:input.project,MAX_BOT_TOKEN:'test-max-token',OMNIA_PUBLIC_APP_ORIGIN:'https://app.test'}},
require:n=>require(n),fetch:async(url,options)=>{sent.push({url,...options});
if(input.failure==='network')throw new Error('test private credential');
return new Response(null,{status:input.failure==='upstream'?503:204});}};
let source=stripTypeScriptTypes(fs.readFileSync(input.helper,'utf8'));
source=source.replace(/import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
(_,b,n)=>`const {${b}}=require('${n}');`);
const exported=[];source=source.replace(/export\s+((?:async\s+)?(?:function|class|const)\s+(\w+))/g,
(_,d,n)=>{exported.push(n);return d;});
vm.runInNewContext(source+'\nObject.assign(exports,{'+exported.join(',')+'});',ctx);
ctx.exports.forwardMaxAnalytics(input.actor,input.id,'action').then(()=>console.log(JSON.stringify(sent)));
"""


def execute(helper, project, actor="42", failure=None):
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps(
            {
                "project": str(project),
                "helper": str(helper),
                "actor": actor,
                "id": str(uuid4()),
                "failure": failure,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert "private credential" not in result.stderr
    return json.loads(result.stdout)


def test_managed_refresh_adds_executable_helper_and_preserves_product(tmp_path):
    project = uuid4()
    old = {"src/app/page.tsx": "export default function Coffee(){return 'Coffee'};"}
    old.update(render_max_managed_files(_config(), project))
    helper = "src/lib/omnia/analytics.ts"
    assert helper in old and helper in MAX_SECURITY_LOCKED_FILES
    assert old["src/app/page.tsx"].endswith("'Coffee'};")
    for path, content in old.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    sent = execute(tmp_path / helper, project)
    assert len(sent) == 1
    assert sent[0]["url"].endswith(f"/api/runtime/projects/{project}/analytics")
    assert json.loads(sent[0]["body"])["kind"] == "action"
    from yleum_api.services.integration_auth import verify_integration_assertion

    assert (
        verify_integration_assertion(
            sent[0]["headers"]["X-Omnia-Integration-Assertion"],
            "test-max-token",
            project_id=project,
            method="POST",
            path=f"/api/runtime/projects/{project}/analytics",
            body=sent[0]["body"].encode(),
        )
        == 42
    )


def test_forwarding_retries_same_receipt_and_never_throws():
    root = Path(__file__).resolve().parents[3]
    helper = root / "apps/orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/analytics.ts"
    for failure in ("network", "upstream"):
        sent = execute(helper, uuid4(), failure=failure)
        assert len(sent) == 2 and sent[0]["body"] == sent[1]["body"]
    assert execute(helper, uuid4(), actor="preview") == []
