# Banyan

Banyan is a personal AI learning system. Enter a topic, get an editable outline, learn it node by
node, branch off into side questions under any node, and end with one self-contained HTML study
guide that merges everything you learned.

Answers are **knowledge-base first**: a question is answered from what you already learned (governed
by [llm-steward](https://github.com/ArthurGao/llm-steward), retrieved by
[lite-rag](https://github.com/ArthurGao/lite-rag)) and only goes to an LLM, with web search, when the
KB is not enough. Claude, GPT, Gemini, DeepSeek and Kimi are all supported through one role layer.

```
backend/    FastAPI service: learning tree, ask pipeline, LLM role layer, steward client, notes, export
frontend/   React + Vite PWA, responsive for phone, tablet and desktop
docs/       design — read docs/banyan-design.md before writing code
```

Status: design only. See `docs/banyan-design.md` §12 for the stage plan.
