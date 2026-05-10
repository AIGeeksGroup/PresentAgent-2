import json
import os
import signal
import threading
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Union
import requests
from qwen_agent.tools.base import BaseTool, register_tool
from openai import OpenAI
import random
from urllib.parse import urlparse, unquote
import time 
from transformers import AutoTokenizer
import tiktoken
import re

PROMPT_MODULE_NAME = os.getenv("PRESENTAGENT_DEEPRESEARCH_PROMPT_MODULE", "prompt_media")
PROMPT_MODULE = importlib.import_module(PROMPT_MODULE_NAME)
EXTRACTOR_PROMPT = PROMPT_MODULE.EXTRACTOR_PROMPT

VISIT_SERVER_TIMEOUT = int(os.getenv("VISIT_SERVER_TIMEOUT", 200))
WEBCONTENT_MAXLENGTH = int(os.getenv("WEBCONTENT_MAXLENGTH", 150000))

JINA_API_KEYS = os.getenv("JINA_API_KEYS", "")


@staticmethod
def truncate_to_tokens(text: str, max_tokens: int = 95000) -> str:
    encoding = tiktoken.get_encoding("cl100k_base")
    
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    
    truncated_tokens = tokens[:max_tokens]
    return encoding.decode(truncated_tokens)

OSS_JSON_FORMAT = """# Response Formats
## visit_content
{"properties":{"rational":{"type":"string","description":"Locate the **specific sections/data** directly related to the user's goal within the webpage content"},"evidence":{"type":"string","description":"Identify and extract the **most relevant information** from the content, never miss any important information, output the **full original context** of the content as far as possible, it can be more than three paragraphs.","summary":{"type":"string","description":"Organize into a concise paragraph with logical flow, prioritizing clarity and judge the contribution of the information to the goal."}}}}"""


@register_tool('visit', allow_overwrite=True)
class Visit(BaseTool):
    # The `description` tells the agent the functionality of this tool.
    name = 'visit'
    description = 'Visit webpage(s) and judge whether the current page itself directly hosts useful slide-level media and extract direct media URLs.'
    # The `parameters` tell the agent what input parameters the tool has.
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": ["string", "array"],
                "items": {
                    "type": "string"
                    },
                "minItems": 1,
                "description": "The URL(s) of the webpage(s) to visit. Can be a single URL or an array of URLs."
        },
        "goal": {
                "type": "string",
                "description": "The goal of the visit for webpage(s)."
        }
        },
        "required": ["url", "goal"]
    }
    # The `call` method is the main function of the tool.
    def call(self, params: Union[str, dict], **kwargs) -> str:
        try:
            url = params["url"]
            goal = params["goal"]
        except:
            return "[Visit] Invalid request format: Input must be a JSON object containing 'url' and 'goal' fields"

        start_time = time.time()
        
        # Create log folder if it doesn't exist
        log_folder = "log"
        os.makedirs(log_folder, exist_ok=True)

        if isinstance(url, str):
            response = self.readpage_jina(url, goal)
        else:
            response = []
            assert isinstance(url, List)
            start_time = time.time()
            for u in url: 
                if time.time() - start_time > 900:
                    cur_response = "The useful information in {url} for user goal {goal} as follows: \n\n".format(url=url, goal=goal)
                    cur_response += "Source usefulness: \n" + "The webpage content could not be processed, and therefore, no source judgement is available." + "\n\n"
                    cur_response += "Is complete page: \nFalse\n\n"
                    cur_response += "Has media: \nFalse\n\n"
                    cur_response += "Media signals: \nno obvious media signal detected\n\n"
                    cur_response += "Media URLs: \nNone\n\n"
                else:
                    try:
                        cur_response = self.readpage_jina(u, goal)
                    except Exception as e:
                        cur_response = f"Error fetching {u}: {str(e)}"
                response.append(cur_response)
            response = "\n=======\n".join(response)
        
        print(f'Visit Result Length {len(response)}; Visit Result Content {response}')
        return response.strip()
        
    def call_server(self, msgs, max_retries=2):
        api_key = os.environ.get("API_KEY")
        url_llm = os.environ.get("API_BASE")
        model_name = os.environ.get("SUMMARY_MODEL_NAME", "")
        client = OpenAI(
            api_key=api_key,
            base_url=url_llm,
        )
        for attempt in range(max_retries):
            try:
                chat_response = client.chat.completions.create(
                    model=model_name,
                    messages=msgs,
                    temperature=0.7
                )
                content = chat_response.choices[0].message.content
                if content:
                    try:
                        json.loads(content)
                    except:
                        # extract json from string 
                        left = content.find('{')
                        right = content.rfind('}') 
                        if left != -1 and right != -1 and left <= right: 
                            content = content[left:right+1]
                    return content
            except Exception as e:
                # print(e)
                if attempt == (max_retries - 1):
                    return ""
                continue


    def jina_readpage(self, url: str) -> str:
        """
        Read webpage content using Jina service.
        
        Args:
            url: The URL to read
            goal: The goal/purpose of reading the page
            
        Returns:
            str: The webpage content or error message
        """
        max_retries = 3
        timeout = 50
        
        for attempt in range(max_retries):
            headers = {
                "Authorization": f"Bearer {JINA_API_KEYS}",
            }
            try:
                response = requests.get(
                    f"https://r.jina.ai/{url}",
                    headers=headers,
                    timeout=timeout
                )
                if response.status_code == 200:
                    webpage_content = response.text
                    return webpage_content
                else:
                    print(response.text)
                    raise ValueError("jina readpage error")
            except Exception as e:
                time.sleep(0.5)
                if attempt == max_retries - 1:
                    return "[visit] Failed to read page."
                
        return "[visit] Failed to read page."

    def html_readpage_jina(self, url: str) -> str:
        max_attempts = 8
        for attempt in range(max_attempts):
            content = self.jina_readpage(url)
            service = "jina"     
            print(service)
            if content and not content.startswith("[visit] Failed to read page.") and content != "[visit] Empty content." and not content.startswith("[document_parser]"):
                return content
        return "[visit] Failed to read page."

    def readpage_jina(self, url: str, goal: str) -> str:
        """
        Attempt to read webpage content by alternating between jina and aidata services.
        
        Args:
            url: The URL to read
            goal: The goal/purpose of reading the page
            
        Returns:
            str: The webpage content or error message
        """
   
        summary_page_func = self.call_server
        max_retries = int(os.getenv('VISIT_SERVER_MAX_RETRIES', 1))

        content = self.html_readpage_jina(url)

        if content and not content.startswith("[visit] Failed to read page.") and content != "[visit] Empty content." and not content.startswith("[document_parser]"):
            content = truncate_to_tokens(content, max_tokens=95000)
            messages = [{"role":"user","content": EXTRACTOR_PROMPT.format(webpage_content=content, goal=goal)}]
            parse_retry_times = 0
            raw = summary_page_func(messages, max_retries=max_retries)
            summary_retries = 3
            while len(raw) < 10 and summary_retries >= 0:
                truncate_length = int(0.7 * len(content)) if summary_retries > 0 else 25000
                status_msg = (
                    f"[visit] Evaluate url[{url}] " 
                    f"attempt {3 - summary_retries + 1}/3, "
                    f"content length: {len(content)}, "
                    f"truncating to {truncate_length} chars"
                ) if summary_retries > 0 else (
                    f"[visit] Evaluate url[{url}] failed after 3 attempts, "
                    f"final truncation to 25000 chars"
                )
                print(status_msg)
                content = content[:truncate_length]
                extraction_prompt = EXTRACTOR_PROMPT.format(
                    webpage_content=content,
                    goal=goal
                )
                messages = [{"role": "user", "content": extraction_prompt}]
                raw = summary_page_func(messages, max_retries=max_retries)
                summary_retries -= 1

            parse_retry_times = 0
            if isinstance(raw, str):
                raw = raw.replace("```json", "").replace("```", "").strip()
            while parse_retry_times < 3:
                try:
                    raw = json.loads(raw)
                    break
                except:
                    raw = summary_page_func(messages, max_retries=max_retries)
                    parse_retry_times += 1
            
            if parse_retry_times >= 3:
                useful_information = "The useful information in {url} for user goal {goal} as follows: \n\n".format(url=url, goal=goal)
                useful_information += "Source usefulness: \n" + "The webpage content could not be processed, and therefore, no source judgement is available." + "\n\n"
                useful_information += "Is complete page: \nFalse\n\n"
                useful_information += "Has media: \nFalse\n\n"
                useful_information += "Media signals: \nno obvious media signal detected\n\n"
                useful_information += "Media URLs: \nNone\n\n"
            else:
                has_media = bool(raw.get("has_media", False))
                media_signals = str(raw.get("media_signals", "")).strip()
                media_urls = raw.get("media_urls", [])
                if not isinstance(media_urls, list):
                    media_urls = []
                media_urls = [str(item).strip() for item in media_urls if str(item).strip()]
                if not media_signals or (not has_media and not media_urls):
                    has_media, media_signals, fallback_media_urls = self._fallback_media_signals(content, url)
                    if not media_urls:
                        media_urls = fallback_media_urls
                useful_information = "The useful information in {url} for user goal {goal} as follows: \n\n".format(url=url, goal=goal)
                useful_information += "Source usefulness: \n" + str(raw.get("source_usefulness", "")) + "\n\n"
                useful_information += "Is complete page: \n" + str(raw.get("is_complete_page", False)) + "\n\n"
                useful_information += "Has media: \n" + str(has_media) + "\n\n"
                useful_information += "Media signals: \n" + media_signals + "\n\n"
                useful_information += "Media URLs: \n" + ("\n".join(media_urls) if media_urls else "None") + "\n\n"

            if len(useful_information) < 10 and summary_retries < 0:
                print("[visit] Could not generate valid visit judgement after maximum retries")
                useful_information = "[visit] Failed to read page"
            
            return useful_information

        # If no valid content was obtained after all retries
        else:
            useful_information = "The useful information in {url} for user goal {goal} as follows: \n\n".format(url=url, goal=goal)
            useful_information += "Source usefulness: \n" + "The webpage content could not be processed, and therefore, no source judgement is available." + "\n\n"
            useful_information += "Is complete page: \nFalse\n\n"
            useful_information += "Has media: \nFalse\n\n"
            useful_information += "Media signals: \nno obvious media signal detected\n\n"
            useful_information += "Media URLs: \nNone\n\n"
            return useful_information

    
    def _extract_media_urls(self, text: str, page_url: str) -> list[str]:
        candidates = re.findall(r'https?://[^\s<>"\']+|//[^\s<>"\']+', text)
        media_hints = ("youtube.com", "youtu.be", "vimeo.com", "x.com", "twitter.com", ".mp4", ".webm", ".mov", ".gif", "video", "animation", "gif", "demo")
        parsed = urlparse(page_url)
        results = []
        seen = set()
        for url in candidates:
            if url.startswith("//"):
                url = f"{parsed.scheme}:{url}"
            url = url.rstrip(").,]>\"'")
            lowered = url.lower()
            if url in seen:
                continue
            if any(hint in lowered for hint in media_hints):
                seen.add(url)
                results.append(url)
        return results[:10]

    def _fallback_media_signals(self, text: str, page_url: str) -> tuple[bool, str, list[str]]:
        lowered = text.lower()
        phrases = []
        if "youtube" in lowered or "youtu.be" in lowered:
            phrases.append("youtube link detected")
        if "vimeo" in lowered:
            phrases.append("vimeo link detected")
        if "x.com/" in lowered or "twitter.com/" in lowered:
            phrases.append("x/twitter media link detected")
        if ".mp4" in lowered or ".webm" in lowered or ".mov" in lowered:
            phrases.append("direct video file link detected")
        if ".gif" in lowered or " gif" in lowered:
            phrases.append("gif mention detected")
        if "animation" in lowered:
            phrases.append("animation mention detected")
        if "video" in lowered:
            phrases.append("video mention detected")
        media_urls = self._extract_media_urls(text, page_url)
        return bool(phrases or media_urls), ("; ".join(phrases) if phrases else "no obvious media signal detected"), media_urls
