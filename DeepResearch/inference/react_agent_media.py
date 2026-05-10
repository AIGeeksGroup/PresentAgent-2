import json
import json5
import os
import importlib
from typing import Dict, Iterator, List, Literal, Optional, Tuple, Union
from qwen_agent.llm.schema import Message
from qwen_agent.utils.utils import build_text_completion_prompt
from openai import OpenAI, APIError, APIConnectionError, APITimeoutError
from transformers import AutoTokenizer 
from datetime import datetime
from qwen_agent.agents.fncall_agent import FnCallAgent
from qwen_agent.llm import BaseChatModel
from qwen_agent.llm.schema import ASSISTANT, DEFAULT_SYSTEM_MESSAGE, Message
from qwen_agent.settings import MAX_LLM_CALL_PER_RUN
from qwen_agent.tools import BaseTool
from qwen_agent.utils.utils import format_as_text_message, merge_generate_cfgs
import time
import asyncio

from tool_file import *
from tool_scholar import *
from tool_python import *
from tool_search import *
from tool_visit_media import *

PROMPT_MODULE_NAME = os.getenv("PRESENTAGENT_DEEPRESEARCH_PROMPT_MODULE", "prompt_media")
PROMPT_MODULE = importlib.import_module(PROMPT_MODULE_NAME)
SYSTEM_PROMPT = PROMPT_MODULE.SYSTEM_PROMPT
VISIT_GOAL_TEMPLATE = getattr(
    PROMPT_MODULE,
    "VISIT_GOAL_TEMPLATE",
    "Determine whether this page is a complete presentation-usable source for '{question}'. "
    "Focus on explanation quality, whether it is an official doc/paper/explainer page, and whether it contains video, gif, animation, demo, or direct media links. "
    "Extract media URLs if present.",
)

OBS_START = '<tool_response>'
OBS_END = '\n</tool_response>'

MAX_LLM_CALL_PER_RUN = int(os.getenv('MAX_LLM_CALL_PER_RUN', 100))
MEDIA_SEARCH_MODE = os.getenv("MEDIA_SEARCH_MODE", "1") == "1"

TOOL_CLASS = [
    FileParser(),
    Scholar(),
    Visit(),
    Search(),
    PythonInterpreter(),
]
TOOL_MAP = {tool.name: tool for tool in TOOL_CLASS}

import random
import datetime
import re


def today_date():
    return datetime.date.today().strftime("%Y-%m-%d")


def extract_media_target(question: str) -> str:
    if not question:
        return ""
    q = question.strip()
    m = re.search(r'"search_query"\s*:\s*"([^"]+)"', q)
    if m:
        return m.group(1).strip()
    m = re.search(r"'search_query'\s*:\s*'([^']+)'", q)
    if m:
        return m.group(1).strip()
    m = re.search(r"User request:\s*(.+)", q, flags=re.IGNORECASE)
    if m:
        line = m.group(1).strip().split("\n")[0].strip()
        line = re.sub(r"^(please\s+)?(explain|show|find|search for)\s+", "", line, flags=re.IGNORECASE)
        return line.strip()
    stale_phrases = [
        "We are preparing a presentation for a user based on a short request.",
        "Your job is to find webpages that can support a presentation",
        "Prioritize HTML pages whose content can realistically be turned into presentation material",
        "Focus on finding HTML links",
        "presentation source material",
        "source material",
        "HTML pages",
        "webpages",
    ]
    for phrase in stale_phrases:
        q = q.replace(phrase, " ")
    lines = [x.strip() for x in q.splitlines() if x.strip()]
    if lines:
        for line in lines:
            if any(k in line.lower() for k in ["video", "demo", "animation", "visualization", "gif", "mp4", "webm"]):
                return line[:240].strip()
        candidates = [x for x in lines if 3 <= len(x) <= 160]
        if candidates:
            return min(candidates, key=len).strip()
    return q[:200].strip()


def split_core_and_descriptors(target: str) -> tuple[str, str]:
    target = re.sub(r"\s+", " ", target).strip()
    tokens = target.split()
    if len(tokens) <= 6:
        return target, ""
    core_len = 2 if len(tokens) >= 2 else len(tokens)
    if len(tokens) >= 4 and tokens[0] and tokens[0][0].isupper():
        core_len = min(4, len(tokens))
    core = " ".join(tokens[:core_len])
    descriptors = " ".join(tokens[core_len:])
    return core.strip(), descriptors.strip()


def build_video_first_queries(question: str) -> list[str]:
    target = extract_media_target(question)
    core, desc = split_core_and_descriptors(target)
    suffix = f" {desc.strip()}" if desc.strip() else ""
    return [
        f'site:x.com "{core}"{suffix} video demo animation visualization gif',
        f'site:twitter.com "{core}"{suffix} video demo animation visualization gif',
        f'site:youtube.com/watch "{core}"{suffix} demo animation visualization walkthrough overview',
        f'site:youtu.be "{core}"{suffix} demo animation visualization walkthrough overview',
        f'site:vimeo.com "{core}"{suffix} demo animation visualization',
        f'site:bilibili.com/video "{core}"{suffix} demo animation visualization',
        f'site:huggingface.co/spaces "{core}"{suffix} demo animation visualization',
        f'site:github.com "{core}"{suffix} demo gif mp4 webm animation',
        f'site:slideslive.com "{core}"{suffix} video',
    ]

class MultiTurnReactAgent(FnCallAgent):
    def __init__(self,
                 function_list: Optional[List[Union[str, Dict, BaseTool]]] = None,
                 llm: Optional[Union[Dict, BaseChatModel]] = None,
                 **kwargs):

        self.llm_generate_cfg = llm["generate_cfg"]
        self.llm_local_path = llm["model"]

    def sanity_check_output(self, content):
        return "<think>" in content and "</think>" in content

    def _normalize_query(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip())

    def _rewrite_search_queries(self, queries, question: str) -> list[str]:
        if isinstance(queries, str):
            queries = [queries]
        normalized = []
        seen = set()
        for query in queries or []:
            query = self._normalize_query(str(query))
            if query and query not in seen:
                seen.add(query)
                normalized.append(query)

        if not normalized:
            normalized = build_video_first_queries(question)
        return normalized[:6]

    def _rewrite_visit_goal(self, question: str) -> str:
        return VISIT_GOAL_TEMPLATE.format(question=extract_media_target(question))
    
    def call_server(self, msgs, planning_port, max_tries=10):
        
        openai_api_key = "EMPTY"
        openai_api_base = f"http://127.0.0.1:{planning_port}/v1"

        client = OpenAI(
            api_key=openai_api_key,
            base_url=openai_api_base,
            timeout=600.0,
        )

        base_sleep_time = 1 
        for attempt in range(max_tries):
            try:
                print(f"--- Attempting to call the service, try {attempt + 1}/{max_tries} ---")
                chat_response = client.chat.completions.create(
                    model=self.model,
                    messages=msgs,
                    stop=["\n<tool_response>", "<tool_response>"],
                    temperature=self.llm_generate_cfg.get('temperature', 0.6),
                    top_p=self.llm_generate_cfg.get('top_p', 0.95),
                    logprobs=True,
                    max_tokens=10000,
                    presence_penalty=self.llm_generate_cfg.get('presence_penalty', 1.1)
                )
                content = chat_response.choices[0].message.content

                # OpenRouter provides API calling. If you want to use OpenRouter, you need to uncomment line 89 - 90.
                # reasoning_content = "<think>\n" + chat_response.choices[0].message.reasoning.strip() + "\n</think>"
                # content = reasoning_content + content                
                
                if content and content.strip():
                    print("--- Service call successful, received a valid response ---")
                    return content.strip()
                else:
                    print(f"Warning: Attempt {attempt + 1} received an empty response.")

            except (APIError, APIConnectionError, APITimeoutError) as e:
                print(f"Error: Attempt {attempt + 1} failed with an API or network error: {e}")
            except Exception as e:
                print(f"Error: Attempt {attempt + 1} failed with an unexpected error: {e}")

            if attempt < max_tries - 1:
                sleep_time = base_sleep_time * (2 ** attempt) + random.uniform(0, 1)
                sleep_time = min(sleep_time, 30) 
                
                print(f"Retrying in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
            else:
                print("Error: All retry attempts have been exhausted. The call has failed.")
        
        return f"vllm server error!!!"

    def count_tokens(self, messages):
        tokenizer = AutoTokenizer.from_pretrained(self.llm_local_path) 
        full_prompt = tokenizer.apply_chat_template(messages, tokenize=False)
        tokens = tokenizer(full_prompt, return_tensors="pt")
        token_count = len(tokens["input_ids"][0])
        
        return token_count

    def _run(self, data: str, model: str, **kwargs) -> List[List[Message]]:
        self.model=model
        self.media_first_search_done = False
        try:
            question = data['item']['question']
        except: 
            raw_msg = data['item']['messages'][1]["content"] 
            question = raw_msg.split("User:")[1].strip() if "User:" in raw_msg else raw_msg 

        start_time = time.time()
        planning_port = data['planning_port']
        answer = data['item']['answer']
        self.user_prompt = question
        system_prompt = SYSTEM_PROMPT
        cur_date = today_date()
        system_prompt = system_prompt + str(cur_date)
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]
        num_llm_calls_available = MAX_LLM_CALL_PER_RUN
        round = 0
        while num_llm_calls_available > 0:
            # Check whether time is reached
            if time.time() - start_time > 150 * 60:  # 150 minutes in seconds
                prediction = 'No answer found after 2h30mins'
                termination = 'No answer found after 2h30mins'
                result = {
                    "question": question,
                    "answer": answer,
                    "messages": messages,
                    "prediction": prediction,
                    "termination": termination
                }
                return result
            round += 1
            num_llm_calls_available -= 1
            content = self.call_server(messages, planning_port)
            print(f'Round {round}: {content}')
            if '<tool_response>' in content:
                pos = content.find('<tool_response>')
                content = content[:pos]
            messages.append({"role": "assistant", "content": content.strip()})
            if '<tool_call>' in content and '</tool_call>' in content:
                tool_call = content.split('<tool_call>')[1].split('</tool_call>')[0]
                try:
                    if "python" in tool_call.lower():
                        try:
                            code_raw=content.split('<tool_call>')[1].split('</tool_call>')[0].split('<code>')[1].split('</code>')[0].strip()
                            result = TOOL_MAP['PythonInterpreter'].call(code_raw)
                        except:
                            result = "[Python Interpreter Error]: Formatting error."

                    else:
                        tool_call = json5.loads(tool_call)
                        tool_name = tool_call.get('name', '')
                        tool_args = tool_call.get('arguments', {})
                        result = self.custom_call_tool(tool_name, tool_args)

                except:
                    result = 'Error: Tool call is not a valid JSON. Tool call must contain a valid "name" and "arguments" field.'
                result = "<tool_response>\n" + result + "\n</tool_response>"
                # print(result)
                messages.append({"role": "user", "content": result})
            if '<answer>' in content and '</answer>' in content:
                termination = 'answer'
                break
            if num_llm_calls_available <= 0 and '<answer>' not in content:
                messages[-1]['content'] = 'Sorry, the number of llm calls exceeds the limit.'

            max_tokens = 110 * 1024
            token_count = self.count_tokens(messages)
            print(f"round: {round}, token count: {token_count}")

            if token_count > max_tokens:
                print(f"Token quantity exceeds the limit: {token_count} > {max_tokens}")
                
                messages[-1]['content'] = "You have now reached the maximum context length you can handle. You should stop making tool calls and, based on all the information above, think again and provide what you consider the most likely answer in the following format:<think>your final thinking</think>\n<answer>your answer</answer>"
                content = self.call_server(messages, planning_port)
                messages.append({"role": "assistant", "content": content.strip()})
                if '<answer>' in content and '</answer>' in content:
                    prediction = messages[-1]['content'].split('<answer>')[1].split('</answer>')[0]
                    termination = 'generate an answer as token limit reached'
                else:
                    prediction = messages[-1]['content']
                    termination = 'format error: generate an answer as token limit reached'
                result = {
                    "question": question,
                    "answer": answer,
                    "messages": messages,
                    "prediction": prediction,
                    "termination": termination
                }
                return result

        if '<answer>' in messages[-1]['content']:
            prediction = messages[-1]['content'].split('<answer>')[1].split('</answer>')[0]
            termination = 'answer'
        else:
            prediction = 'No answer found.'
            termination = 'answer not found'
            if num_llm_calls_available == 0:
                termination = 'exceed available llm calls'
        result = {
            "question": question,
            "answer": answer,
            "messages": messages,
            "prediction": prediction,
            "termination": termination
        }
        return result

    def custom_call_tool(self, tool_name: str, tool_args: dict, **kwargs):
        if tool_name in TOOL_MAP:
            if MEDIA_SEARCH_MODE and tool_name == "search" and not getattr(self, "media_first_search_done", False):
                forced_queries = build_video_first_queries(getattr(self, "user_prompt", ""))
                tool_args = {"query": forced_queries}
                self.media_first_search_done = True
                print("[media-search] Overriding first search with platform-specific video queries:")
                for q in forced_queries:
                    print("  -", q)
            tool_args["params"] = tool_args
            if tool_name == "search":
                tool_args["query"] = self._rewrite_search_queries(tool_args.get("query", []), self.user_prompt)
            elif MEDIA_SEARCH_MODE and tool_name == "visit":
                try:
                    media_target = extract_media_target(getattr(self, "user_prompt", ""))
                    tool_args["goal"] = VISIT_GOAL_TEMPLATE.format(question=media_target)
                    print("[media-search] Rewriting visit goal for direct playable media evaluation.")
                except Exception as e:
                    print(f"[media-search] Failed to rewrite visit goal: {e}")
            elif tool_name == "visit":
                tool_args["goal"] = self._rewrite_visit_goal(self.user_prompt)
            if "python" in tool_name.lower():
                result = TOOL_MAP['PythonInterpreter'].call(tool_args)
            elif tool_name == "parse_file":
                params = {"files": tool_args["files"]}
                
                raw_result = asyncio.run(TOOL_MAP[tool_name].call(params, file_root_path="./eval_data/file_corpus"))
                result = raw_result

                if not isinstance(raw_result, str):
                    result = str(raw_result)
            else:
                raw_result = TOOL_MAP[tool_name].call(tool_args, **kwargs)
                result = raw_result
            return result

        else:
            return f"Error: Tool {tool_name} not found"
