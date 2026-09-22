#!/usr/bin/env python3
import json, sys
thread = "thr_fake"
turn = "turn_fake"
thread_name = None
thread_goal = None
metadata = {"isPinned": False, "gitInfo": {}}
has_dynamic_tools = False
pending_dynamic = False
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    rid = m.get("id")
    if rid == 901 and method is None:
        result = m.get("result") or {}
        decision = result.get("decision") if isinstance(result, dict) else None
        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,"itemId":"i1","delta":"approval:"+str(decision)}}), flush=True)
        print(json.dumps({"method":"turn/completed","params":{"threadId":thread,"turn":{"id":turn,"status":"completed"}}}), flush=True)
        continue
    if rid == 900 and method is None:
        result = m.get("result") or {}
        ok = bool(result.get("success"))
        text = ((result.get("contentItems") or [{}])[0].get("text") or "") if isinstance(result, dict) else ""
        print(json.dumps({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"item":{"id":"dyn1","type":"dynamicToolCall","tool":"status","arguments":{},"status":"completed" if ok else "failed","contentItems":result.get("contentItems") or [],"success":ok}}}), flush=True)
        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,"itemId":"i1","delta":"dynamic:"+text[:120]}}), flush=True)
        print(json.dumps({"method":"turn/completed","params":{"threadId":thread,"turn":{"id":turn,"status":"completed"}}}), flush=True)
        continue
    if rid is None:
        continue
    if method == "model/list":
        print(json.dumps({"id":rid,"result":{
            "data":[{
                "id":"gpt-5.6-sol",
                "model":"gpt-5.6-sol",
                "displayName":"GPT-5.6 Sol",
                "description":"fake",
                "hidden":False,
                "supportedReasoningEfforts":[
                    {"reasoningEffort":"low","description":"low"},
                    {"reasoningEffort":"medium","description":"medium"},
                    {"reasoningEffort":"high","description":"high"},
                    {"reasoningEffort":"xhigh","description":"xhigh"}
                ],
                "defaultReasoningEffort":"high",
                "isDefault":True
            }],
            "nextCursor":None
        }}), flush=True)
    elif method == "initialize":
        # Real app-server may emit notifications before initialize completes.
        print(json.dumps({"method":"account/updated","params":{"account":None}}), flush=True)
        print(json.dumps({"id":rid,"result":{"userAgent":"fake"}}), flush=True)
    elif method == "configRequirements/read":
        print(json.dumps({"id":rid,"result":{"requirements":{"allowedApprovalPolicies":["never","unlessTrusted","onRequest"],"allowedSandboxModes":["workspace-write","read-only","readOnly"]}}}), flush=True)
    elif method == "thread/start":
        params=m.get("params",{})
        has_dynamic_tools=bool(params.get("dynamicTools"))
        expected="readOnly" if has_dynamic_tools else "workspaceWrite"
        if params.get("sandbox") != expected:
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad sandbox mode"}}), flush=True)
        elif params.get("model") != "gpt-5.6-sol":
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad model"}}), flush=True)
        elif params.get("approvalPolicy") != "unlessTrusted":
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad approval policy"}}), flush=True)
        else:
            print(json.dumps({"id":rid,"result":{
                "thread":{
                    "id":thread,
                    "model":params.get("model") or "gpt-5.6-sol",
                    "reasoningEffort":"high"
                },
                "model":params.get("model") or "gpt-5.6-sol",
                "reasoningEffort":"high",
                "approvalPolicy":params.get("approvalPolicy"),
                "sandbox":{"type":params.get("sandbox")}
            }}), flush=True)
    elif method == "thread/resume":
        print(json.dumps({"id":rid,"result":{
            "thread":{
                "id":thread,
                "name":thread_name,
                "model":"gpt-5.6-sol",
                "reasoningEffort":"high"
            },
            "model":"gpt-5.6-sol",
            "reasoningEffort":"high",
            "approvalPolicy":"unlessTrusted",
            "sandbox":{"type":"workspaceWrite"}
        }}), flush=True)
    elif method == "turn/start":
        params=m.get("params",{})
        policy=params.get("sandboxPolicy",{}).get("type")
        expected_policy="readOnly" if has_dynamic_tools else "workspaceWrite"
        if policy != expected_policy:
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad sandbox policy"}}), flush=True)
            continue
        if params.get("model") != "gpt-5.6-sol" or params.get("effort") != "high":
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad model effort"}}), flush=True)
            continue
        if params.get("approvalPolicy") != "unlessTrusted":
            print(json.dumps({"id":rid,"error":{"code":-32602,"message":"bad turn approval policy"}}), flush=True)
            continue
        print(json.dumps({"id":rid,"result":{"turn":{"id":turn,"status":"inProgress","items":[]}}}), flush=True)
        print(json.dumps({"method":"turn/started","params":{"threadId":thread,"turn":{"id":turn,"status":"inProgress"}}}), flush=True)
        prompt_text=" ".join(
            str(item.get("text") or "")
            for item in (params.get("input") or [])
            if isinstance(item, dict)
        )
        if "approval-command" in prompt_text:
            print(json.dumps({
                "id":901,
                "method":"item/commandExecution/requestApproval",
                "params":{
                    "threadId":thread,
                    "turnId":turn,
                    "itemId":"cmd1",
                    "command":"echo approved",
                    "cwd":params.get("cwd"),
                    "reason":"test approval",
                    "availableDecisions":["accept","decline"]
                }
            }), flush=True)
        elif has_dynamic_tools:
            print(json.dumps({"method":"item/started","params":{"threadId":thread,"turnId":turn,"item":{"id":"dyn1","type":"dynamicToolCall","namespace":"reasonfirst_remote","tool":"status","arguments":{},"status":"inProgress"}}}), flush=True)
            print(json.dumps({"id":900,"method":"item/tool/call","params":{"threadId":thread,"turnId":turn,"callId":"dyn1","namespace":"reasonfirst_remote","tool":"status","arguments":{}}}), flush=True)
        else:
            print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,"itemId":"i1","delta":"fake ok"}}), flush=True)
            print(json.dumps({"method":"turn/completed","params":{"threadId":thread,"turn":{"id":turn,"status":"completed"}}}), flush=True)
    elif method == "thread/name/set":
        thread_name = m.get("params",{}).get("name")
        print(json.dumps({"id":rid,"result":{}}), flush=True)
        print(json.dumps({"method":"thread/name/updated","params":{"threadId":thread,"name":thread_name}}), flush=True)
    elif method == "thread/goal/set":
        thread_goal = m.get("params",{}).get("objective")
        print(json.dumps({"id":rid,"result":{"goal":{"threadId":thread,"objective":thread_goal,"status":"active"}}}), flush=True)
    elif method == "thread/metadata/update":
        params=m.get("params",{})
        metadata["isPinned"]=params.get("isPinned", metadata["isPinned"])
        metadata["gitInfo"].update(params.get("gitInfo") or {})
        print(json.dumps({"id":rid,"result":{"thread":{"id":thread,"isPinned":metadata["isPinned"],"gitInfo":metadata["gitInfo"]}}}), flush=True)
    elif method == "thread/list":
        print(json.dumps({"id":rid,"result":{"data":[{"id":thread,"name":thread_name,"isPinned":metadata["isPinned"],"source":"appServer"}],"nextCursor":None}}), flush=True)
    elif method == "turn/steer":
        print(json.dumps({"id":rid,"result":{"turnId":turn}}), flush=True)
    elif method == "turn/interrupt":
        print(json.dumps({"id":rid,"result":{}}), flush=True)
    elif method == "thread/read":
        print(json.dumps({"id":rid,"result":{"thread":{"id":thread,"name":thread_name,"isPinned":metadata["isPinned"],"gitInfo":metadata["gitInfo"],"status":{"type":"idle"}}}}), flush=True)
    else:
        print(json.dumps({"id":rid,"error":{"code":-1,"message":"unsupported"}}), flush=True)
