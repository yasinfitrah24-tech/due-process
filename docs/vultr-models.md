# Vultr Serverless Inference models

From `GET https://api.vultrinference.com/v1/models` on 2026-11-04, schema version 2.4, 35 models.

Every column comes from a field in the response. `-` means the response has no value for that model.
Image input means `input_modalities` lists `image`; the column shows its `sources`. That is what the response
claims, not something tested here, except where `docs/model-bakeoff.md` says so.

| id | name | input | output | image input | max context (tokens) | quantization | prompt USD per token |
|---|---|---|---|---|---|---|---|
| `ace-step-1.5` | ACE-Step 1.5 | text | audio | - | - | - | - |
| `auk` | AuK (evaluation) | text | speech | - | - | - | - |
| `auk-flash` | AuK-Flash (evaluation) | text | speech | - | - | - | - |
| `bge-reranker-v2-m3` | BGE M3 | text | rerank | - | 8,192 | fp16 | - |
| `chatterbox` | Chatterbox | text | speech | - | - | - | - |
| `clef` | Clef | text, image | decisions | base64 | 262,144 | bf16 | - |
| `clef-flash` | Clef Flash | text, image | decisions | base64 | 262,144 | bf16 | - |
| `d3` | d3 | text, image | decisions | base64 | 262,144 | bf16 | - |
| `deepseek-v4.1-flash-1017` | DeepSeek V4.1 Flash 1017 | text, image, file | text | url, base64 | 1,048,576 | nvfp4 | 0.00000015 |
| `glm-5.2` | GLM 5.2 | text, image, file | text | url, base64 | 1,048,576 | nvfp4 | 0.00000075 |
| `glm-5.3` | GLM 5.3 | text, image, file | text | url, base64 | 1,048,576 | nvfp4 | 0.00000075 |
| `glm-5.3-flash` | GLM 5.3 Flash | text, image, file, video | text | url, base64 | 1,048,576 | nvfp4 | 0.0000001 |
| `granite-speech-5.0-2b` | Granite Speech 5.0 2B | audio | transcription | - | - | bf16 | - |
| `mimo-v2.6-flash` | MiMo V2.6 Flash | text, image, file, audio, video | text | url, base64 | 1,048,576 | fp8 | 0.0000001 |
| `mimo-v2.6-pro` | MiMo V2.6 Pro | text, image, file, audio, video | text | url, base64 | 1,048,576 | fp8 | 0.0000004 |
| `minimax-h3` | MiniMax H3 | text, image, video, audio | video, image | url, base64 | - | - | - |
| `minimax-m3.1-flash` | MiniMax M3.1 Flash | text, image, file, video | text | url, base64 | 1,048,576 | nvfp4 | 0.0000002 |
| `minimax-music3` | MiniMax Music 3 | text | audio | - | - | bf16 | - |
| `moss-image` | MOSS-Image | text | image | - | - | bf16 | - |
| `muse-glimmer-30b` | Muse Glimmer 30B | text, image, file, video | text | url, base64 | 131,072 | nvfp4 | 0.00000025 |
| `nemotron-3-nano-omni-30b-a3b-reasoning` | Nemotron 3 Nano Omni | text, image, file, audio, video | text | url, base64 | 262,144 | bf16 | 0.0000001 |
| `nemotron-3.5-content-safety` | Nemotron 3.5 Content Safety | text, image, file | text | url, base64 | 131,072 | bf16 | 0.00000005 |
| `pplx-decider-v1.2` | pplx-decider v1.2 | text, image | decisions | base64 | 262,144 | bf16 | - |
| `qwen-image-2.1` | Qwen-Image 2.1 (evaluation) | text, image | image | url, base64 | - | bf16 | - |
| `qwen3-asr-1.7b` | Qwen3-ASR 1.7B | audio | transcription | - | - | bf16 | - |
| `qwen3-embedding-4b` | Qwen 3 Embedding 4B | text | embeddings | - | 32,768 | fp16 | - |
| `qwen3-tts-12hz-1.7b` | Qwen 3 TTS 12Hz 1.7B | text | speech | - | - | - | - |
| `qwen3.8-27b` | Qwen 3.8 27B | text, image, file, video | text | url, base64 | 262,144 | fp8 | 0.00000015 |
| `qwen3.8-flash-next` | Qwen 3.8 Flash Next | text, image, file, video | text | url, base64 | 262,144 | nvfp4 | 0.0000001 |
| `raon-1-mini-preview` | Raon-1 mini (preview) | text, image, file | text | url, base64 | 262,144 | mxfp8 | 0.0000001 |
| `voxtral-mini-4b-realtime` | Voxtral Mini 4B Realtime | audio | transcription | - | - | bf16 | - |
| `vultron-retriever-core-qwen3.5-4.5b` | Vultron Retriever Core 4.5B | text | rerank | - | 262,144 | bf16 | - |
| `vultron-retriever-flash-qwen3.5-0.8b` | Vultron Retriever Flash 0.8B | text | rerank | - | 262,144 | bf16 | - |
| `whisper-large-v3-turbo` | Whisper Large V3 Turbo | audio | transcription | - | - | fp16 | - |
| `z-image-turbo` | Z-Image Turbo | text | image | - | - | bf16 | - |
