import json
import uuid
import struct
import requests
from typing import List, Dict, Any, Optional


class KiroStreamError(Exception):
    """Raised when the backend returns a non-event-stream error payload."""

    def __init__(self, message: str, status_code: Optional[int] = None, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}

class KiroAPI:
    """
    Kiro API Client for interacting with telemetry and assistant response generation.
    Based on the provided guide and captured HTTP requests.
    """
    def __init__(self, auth_token: str, machine_id: str, profile_arn: str):
        """
        Initialize the KiroAPI client.
        
        :param auth_token: Bearer token for authorization.
        :param machine_id: Unique machine identifier used in headers.
        :param profile_arn: Profile ARN required in the payload.
        """
        self.auth_token = auth_token
        self.machine_id = machine_id
        self.profile_arn = profile_arn
        
        # Endpoints
        self.metrics_url = "https://prod.us-east-1.telemetry.desktop.kiro.dev/v1/metrics"
        self.api_url = "https://q.us-east-1.amazonaws.com/generateAssistantResponse"
        
        # User Agents
        self.user_agent = f"aws-sdk-js/1.0.34 ua/2.1 os/win32#10.0.26200 lang/js md/nodejs#22.22.0 api/codewhispererstreaming#1.0.34 m/E KiroIDE-0.11.131-{self.machine_id}"
        self.amz_user_agent = f"aws-sdk-js/1.0.34 KiroIDE-0.11.131-{self.machine_id}"

    def send_metrics(self, resource_metrics: List[Dict[str, Any]]) -> requests.Response:
        """
        Send telemetry metrics to Kiro backend.
        
        :param resource_metrics: A list of metric objects.
        :return: Response from the metrics endpoint.
        """
        headers = {
            "x-kiro-machineid": self.machine_id,
            "User-Agent": "OTel-OTLP-Exporter-JavaScript/0.57.2",
            "Content-Type": "application/json"
        }
        
        payload = {
            "resourceMetrics": resource_metrics
        }
        
        return requests.post(self.metrics_url, headers=headers, json=payload)

    def _get_base_headers(self, agent_mode: str) -> Dict[str, str]:
        """
        Generate base headers required for AWS API calls.
        """
        return {
            "content-type": "application/json",
            "x-amzn-codewhisperer-optout": "true",
            "x-amzn-kiro-agent-mode": agent_mode,
            "x-amz-user-agent": self.amz_user_agent,
            "user-agent": self.user_agent,
            "amz-sdk-invocation-id": str(uuid.uuid4()),
            "amz-sdk-request": "attempt=1; max=3",
            "Authorization": f"Bearer {self.auth_token}"
        }

    def generate_assistant_response(self, 
                                    content: str, 
                                    conversation_id: str, 
                                    agent_continuation_id: str,
                                    history: Optional[List[Dict[str, Any]]] = None,
                                    model_id: str = "claude-sonnet-4.5",
                                    agent_task_type: str = "vibe",
                                    agent_mode: str = "vibe",
                                    tools: Optional[List[Dict[str, Any]]] = None,
                                    tool_results: Optional[List[Dict[str, Any]]] = None,
                                    stream: bool = True) -> requests.Response:
        """
        Generate an assistant response from Kiro AI.
        
        :param content: The prompt/content from the user.
        :param conversation_id: The conversation ID UUID.
        :param agent_continuation_id: The agent continuation ID UUID.
        :param history: List of previous conversation messages.
        :param model_id: The model ID (e.g. claude-sonnet-4.5).
        :param agent_task_type: Agent task type (e.g. vibe).
        :param agent_mode: Agent mode (e.g. vibe, intent-classification).
        :param tools: Optional list of tools specs available for the agent.
        :param tool_results: Optional list of tool results for the current message.
        :param stream: Set to True if reading a chunked response.
        :return: The requests.Response object.
        """
        headers = self._get_base_headers(agent_mode=agent_mode)
        
        user_input_message_context = {}
        if tools:
            user_input_message_context["tools"] = tools
        if tool_results:
            user_input_message_context["toolResults"] = tool_results

        payload = {
            "conversationState": {
                "agentContinuationId": agent_continuation_id,
                "agentTaskType": agent_task_type,
                "chatTriggerType": "MANUAL",
                "conversationId": conversation_id,
                "currentMessage": {
                    "userInputMessage": {
                        "content": content,
                        "modelId": model_id,
                        "origin": "AI_EDITOR",
                        "userInputMessageContext": user_input_message_context
                    }
                },
                "history": history or []
            },
            "profileArn": self.profile_arn
        }

        return requests.post(self.api_url, headers=headers, json=payload, stream=stream)

    def classify_intent(self, 
                       content: str, 
                       conversation_id: str, 
                       agent_continuation_id: str,
                       history: Optional[List[Dict[str, Any]]] = None) -> requests.Response:
        """
        Convenience method to classify user intent (Do mode vs Spec mode).
        This calls `generate_assistant_response` using the intent-classification mode and simple-task model.
        
        :param content: The user message/prompt content.
        :param conversation_id: The conversation ID UUID.
        :param agent_continuation_id: The agent continuation ID UUID.
        :param history: Prior conversation messages for context.
        :return: Response containing the classification JSON.
        """
        return self.generate_assistant_response(
            content=content,
            conversation_id=conversation_id,
            agent_continuation_id=agent_continuation_id,
            history=history,
            model_id="simple-task",
            agent_task_type="vibe",
            agent_mode="intent-classification",
            tools=None,
            stream=True
        )

    def parse_stream(self, response: requests.Response, debug=False):
        """
        Parses the binary AWS Event Stream from the response and yields JSON payloads.
        """
        buffer = b''
        chunk_count = 0
        event_count = 0
        
        for chunk in response.iter_content(chunk_size=4096):
            chunk_count += 1
            if chunk:
                buffer += chunk
                if debug:
                    print(f"  [PARSE] chunk #{chunk_count}: {len(chunk)} bytes, buffer now {len(buffer)} bytes", flush=True)
            
            while len(buffer) >= 12:
                total_length, header_length, prelude_crc = struct.unpack('>III', buffer[:12])
                
                if debug:
                    print(f"  [PARSE] msg total={total_length} headers={header_length} buffer={len(buffer)}", flush=True)
                
                if total_length < 16 or total_length > 1048576:
                    # Invalid frame - likely not binary event stream
                    error_text = buffer.decode('utf-8', errors='replace')
                    error_payload = None
                    error_message = error_text.strip() or "Kiro returned a non-stream response"

                    try:
                        error_payload = json.loads(error_text)
                        error_message = error_payload.get("message") or error_payload.get("error") or error_message
                    except Exception:
                        pass

                    if debug:
                        print(f"  [PARSE] INVALID frame size {total_length}, dumping buffer as text:", flush=True)
                        print(f"  [PARSE] TEXT: {error_text[:500]}", flush=True)
                    raise KiroStreamError(error_message, status_code=response.status_code, payload=error_payload)
                
                if len(buffer) < total_length:
                    break  # Wait for more data
                    
                message = buffer[:total_length]
                buffer = buffer[total_length:]
                
                payload_length = total_length - header_length - 16
                if payload_length > 0:
                    payload_offset = 12 + header_length
                    payload = message[payload_offset:payload_offset+payload_length]
                    
                    try:
                        parsed = json.loads(payload.decode('utf-8'))
                        event_count += 1
                        if debug:
                            print(f"  [PARSE] event #{event_count}: {list(parsed.keys())}", flush=True)
                        yield parsed
                    except Exception as e:
                        if debug:
                            print(f"  [PARSE] JSON parse error: {e}", flush=True)
                            print(f"  [PARSE] payload bytes: {payload[:200]}", flush=True)
                elif debug:
                    print(f"  [PARSE] empty payload (length={payload_length})", flush=True)
        
        if debug:
            print(f"  [PARSE] DONE: {chunk_count} chunks, {event_count} events, {len(buffer)} bytes remaining", flush=True)
            if buffer:
                try:
                    print(f"  [PARSE] remaining buffer: {buffer[:300].decode('utf-8', errors='replace')}", flush=True)
                except:
                    pass

# Example Usage
if __name__ == "__main__":
    # You will need to extract these from your session/environment
    AUTH_TOKEN = "aoaAAAAAGnucUwa8G5Y90ns2CEZ-p2DC4Dr2xB5JjetZMai5DdJefGtaD3O5KBKheU9vllGQLul4jreRwGo-tifOcBkc0:MGQCMFLK8goLnBLz6jcJuQOzOT5vTh1CDE6BGNRTxDv1HHnSNX5s1LDTn2egJoh8Rq8wDwIwNfalCt2RoMebVe7xP2B7IwrtyoEwc7qTdtCH3cMGHg0H1FMdFeh2hKOCS2jJyJ4r"
    MACHINE_ID = "6b0743b08cb894f098bcc462392c9224eceacc625b3e491feaec8eb9c734a989"
    PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"
    
    api = KiroAPI(auth_token=AUTH_TOKEN, machine_id=MACHINE_ID, profile_arn=PROFILE_ARN)
    
    # 1. Send Metrics Example
    metrics_payload = [{
        "resource": {
            "attributes": [
                {"key": "service.name", "value": {"stringValue": "kiroAgent"}},
                {"key": "telemetry.sdk.language", "value": {"stringValue": "nodejs"}}
            ],
            "droppedAttributesCount": 0
        },
        "scopeMetrics": [] # Populated with your actual metrics scope
    }]
    # res = api.send_metrics(metrics_payload)
    # print(res.status_code, res.text)
    
    # 2. Intent Classification Example
    conversation_id = "ebbc196c-6b4a-4e26-825e-a042ee07bc73"
    continuation_id = "58b89c57-5cd4-481f-96fd-3c0c1cafd9e1"
    
    history_mock = [{
        "userInputMessage": {
            "content": "You are an intent classifier...",
            "modelId": "simple-task",
            "origin": "AI_EDITOR"
        }
    }, {
        "assistantResponseMessage": {
            "content": "I will follow these instructions",
            "toolUses": []
        }
    }]
    
    response = api.classify_intent("write simple article on hello world exactly 40 characters", conversation_id, continuation_id, history_mock)
    print("Status Code:", response.status_code)
    
    print("Stream Events:")
    for event in api.parse_stream(response):
        print(event)
