# Crush Monitor

[简体中文](README.md) · English

An AI-powered tool for looking at conversations with your crush or partner. It helps you make sense of emotions and intentions, and spot replies you could have worded better.

AI doesn't know your relationship or what happens outside the chat. Take the results lightly—as another perspective. Your own judgment and an honest conversation still matter more.

## Features

- **WeChat-style conversation view:** analysis sits beneath each message.
- **Emotions and intentions:** the top three probabilities from 12 emotion and 35 intention categories.
- **Affection score and reply grades:** a conversation-level score, SSS–D grades for your replies, and suggested next steps.
- **Ongoing analysis:** paste more messages to continue. Overlapping excerpts are detected, long conversations run in batches, and results survive a page refresh.
- **Run locally with your own key:** choose TypeSafe, Vercel AI Gateway, OpenRouter or DeepSeek and use your own API credits. No hosted deployment required.

The interface and analysis labels are currently in Chinese. This README provides English setup instructions; it does not add an English UI.

## Why multiple model options?

The first three providers expose Jev's structured judgment API, which returns classifications, scores and probabilities. DeepSeek uses the official Chat Completions API with JSON Output, then adapts the model's answers to the same result format used by the app.

- [Launch post by founder Diogo Almeida](https://x.com/CompleteSkeptic/status/2099925682726002904)
- [Official introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

## Get an API key

Choose **one** provider below. You only need one account and do not need a Vercel-hosted website.

| Provider | Create a key | Setup choice | Free credits |
| --- | --- | --- | --- |
| TypeSafe | Sign in to the [TypeSafe console](https://console.typesafe.ai/), create a key under API Keys and copy it | `typesafe` | New users previously received **$5 in trial credits**; check the console for current availability and amount |
| Vercel AI Gateway | Sign in to Vercel, open [AI Gateway → API Keys](https://vercel.com/d?to=/%5Bteam%5D/~/ai-gateway/api-keys) and select **Create key**. Use an AI Gateway key, not a Vercel account Access Token | `vercel` | **$5/month** on the free tier; card verification required. Purchasing credits ends the monthly free grant ([details](https://vercel.com/docs/ai-gateway/pricing)) |
| OpenRouter | Sign in to [OpenRouter Keys](https://openrouter.ai/settings/keys), select **Create Key** and copy the new key | `openrouter` | A small new-user trial allowance, with no fixed amount publicly specified. [Jev is paid](https://openrouter.ai/typesafe/jev-1.13/), not a free model ([details](https://openrouter.ai/support/)) |
| DeepSeek | Sign in to [DeepSeek API Keys](https://platform.deepseek.com/api_keys), create a key and copy it | `deepseek` | Billed according to the DeepSeek account's model and pricing rules; check the console for current details |

Vercel AI Gateway currently requires a valid credit card on the account, including for free usage. Without verification, requests return 403.

Credits and promotions can change, so check the selected provider's dashboard. DeepSeek defaults to `deepseek-flash`; set `DEEPSEEK_MODEL` if you need another available model.

## Run locally

Install Node.js 22.12+. Download or clone this repository, then run these commands in the project directory. The same commands work on macOS, Windows and Linux.

```sh
npm ci
npm run setup -- --en
```

**Choose the provider, then paste its key.** Key input is hidden and saved in the local `.env`. The endpoint and model are set automatically. Setup sends one synthetic test request to verify the connection, using a small amount of API credits without reading your chats.

```sh
npm run build
npm start
```

Open **http://127.0.0.1:3178/** and leave the terminal running. Next time, just run `npm start`. To change providers or keys, run setup again and restart the service; no rebuild is required.

### Built-in settings and file import

Open **聊天设置** (Chat settings) in the page to choose TypeSafe, Vercel AI
Gateway, OpenRouter or DeepSeek, enter an API key, and click **保存 API 配置**
(Save API configuration). The key is stored only in the local configuration
and is never shown again. The health status refreshes immediately, so no
restart is required. DeepSeek also accepts a model name; blank uses
`deepseek-flash`.

The **导入文件** (Import file) button accepts `.txt`, `.md`, `.csv`, `.html`
and `.pdf`. HTML imports readable text, while PDFs need a text layer; scanned
image-only PDFs show an actionable extraction error. Each file is limited to
250,000 characters. Import does not require literal "me/other" labels or manual
identity confirmation: it reuses known identities, detects obvious self labels,
or defaults to the first speaker. You can swap identities from chat settings.
Pasted text remains supported.

### Windows portable executable

From the source directory, run:

```powershell
npm run package:win
```

This creates `release/crush-monitor.exe`. It is an Electron portable executable:
double-clicking it opens the embedded window, without a console window or an
external browser. The target computer does not need Node.js installed. On first
run, choose a provider and enter the key in Chat settings → Built-in API config;
editing `.env` is not required. `.env` remains supported for compatibility and
must sit beside the exe when used. Chat history remains in the local database.

When the app starts, it makes a read-only check of the upstream
[`FerryCorleone/crush-monitor`](https://github.com/FerryCorleone/crush-monitor)
`main` branch. If a newer commit is found, the page shows a banner with a
GitHub comparison link. It never downloads, overwrites or executes upstream
files. The result is cached for six hours, and a network failure does not
prevent the local page from opening.

<details>
<summary>Manual configuration / upgrading an existing installation</summary>

Alternatively, copy `.env.example` to `.env` and edit only these two lines:

```dotenv
JEV_PROVIDER=deepseek
JEV_API_KEY=your_deepseek_key
DEEPSEEK_MODEL=deepseek-flash
```

Allowed providers: `typesafe`, `vercel`, `openrouter`, `deepseek`. The key must belong to the selected provider. Do not add an API URL. DeepSeek defaults to `deepseek-flash`; set `DEEPSEEK_MODEL=...` only when you need another model. The old `TYPESAFE_API_KEY=...` configuration still works without changes when staying with TypeSafe.

`JEV_API_KEY` takes precedence. If unset, the selected provider uses `TYPESAFE_API_KEY`, `AI_GATEWAY_API_KEY`, `OPENROUTER_API_KEY` or `DEEPSEEK_API_KEY`, respectively. Credentials are never borrowed from a different provider. Shell environment variables take precedence over `.env`; remove stale shell settings if necessary.

</details>

Run `npm run check:api` to retry the connection test. For 401, check the key; 402, credits; 403, model permissions; 429, rate limits. For network errors, check connectivity to the selected provider. Setup reports saved configuration and successful verification separately.

## Usage

1. Copy your conversation, or open a text export and copy its contents. Paste into the input field.
2. Select your own name and click **开始分析** (Analyze). Relationship settings are available in **聊天设置** (Chat settings).
3. Read the emotion, intention and reply labels. Click a label for details.
4. Paste new messages to continue the conversation.

### Supported text formats

| Source | What to paste |
| --- | --- |
| WeChat | Desktop multi-message copy: name, Chinese date/time, then message body on separate lines |
| QQ | `Name: 09-17 19:26:53`, followed by the body on the next line; dates with a year also work |
| WhatsApp | [Export a chat](https://faq.whatsapp.com/1180414079177245/), open the `.txt` file and copy its contents; the two common layouts below are supported |
| iMessage / other apps | Format each message as `Name: body`; English names and names containing spaces work |

```text
[9/17/26, 7:26:53 PM] Alex: Dinner tonight?
[9/17/26, 7:27:00 PM] Me: Sounds good
```

```text
17/09/2026, 19:26 - Alex: Dinner tonight?
17/09/2026, 19:27 - Me: Sounds good
```

If copying from iMessage or another app gives you only the message bodies, add `Alex:` / `Me:` yourself. The app cannot recover missing sender information. Native iMessage bulk-copy compatibility has not been verified; only the manually labelled text format is supported. WhatsApp exports can vary by locale and version. The formats above have automated parser tests, not end-to-end verification on every client.

Multiline bodies and consecutive messages from the same person are preserved. Dates are kept as copied: the parser does not guess day/month order or missing years. Only two-person text conversations are supported—not images, audio, ZIP/HTML exports or chat databases. The app does not monitor messaging apps in the background.

## Notes

- The affection score combines six dimensions: keeping the conversation going, engagement, care, openness, intimacy and concrete actions. Click the score for a breakdown. An explicit refusal that still applies limits the score. **It is not the probability that someone likes you.**
- Long conversations are processed in batches; the full history is not capped at 500 messages. New imports analyze new content and revisit recent messages from the other person. Previous grades for your own replies are retained.
- Scoring uses recent messages and relevant original excerpts from history, including invitations, care, refusals and retractions. Old scores are not evidence for new scores. Retrieval can miss context.
- Each model request stays within 500 messages and 12,000 text characters. Overlong individual messages are retained but need splitting before analysis. Paste at most 250,000 characters at a time; total history depends on browser storage capacity.
- Chats and results stay in this browser's local database. **清空聊天，重新开始** (Clear chat and start over) deletes them. Other browsers or URL ports do not share the same data; clearing browser data also removes it.
- Original messages needed for analysis are sent to your selected platform and its model provider using your account's credits. Local storage does not mean offline inference.
- If analysis fails, check the terminal, API key and account credits. Never commit `.env` or private conversations.

## Development

React + TypeScript + Vite + Express. The same emotion, intention and scoring rules run through four provider endpoints:

| Provider | API | Model |
| --- | --- | --- |
| TypeSafe | [System One](https://docs.typesafe.ai/api) | `jev-1.13.0` |
| Vercel | [TypeSafe-compatible API](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe) | `typesafe-ai/jev` |
| OpenRouter | [Decisions (Alpha)](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request) | `typesafe/jev-1.13` |
| DeepSeek | [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) + [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) | `deepseek-flash` (configurable) |

Native Jev probabilities and confidence are preserved for the first three providers. DeepSeek probabilities and confidence are model-estimated from the supplied criteria, not native Jev probabilities or token logprobs. Every provider result passes the same completeness checks, and results are not guaranteed to be identical. Offline tests cover all four adapters and the analysis pipeline. Run `npm run check:api` to verify your own access.

```sh
npm run dev        # http://127.0.0.1:5178/
npm test           # local tests; no model calls
npm run package:win # build release/crush-monitor.exe for Windows
npm run check:api  # verify the selected provider; uses a small amount of API credits
npm run check:live # full analysis with sample chat; uses the selected provider's credits
```

## License

[MIT](LICENSE). Not affiliated with WeChat, Tencent, TypeSafe or any messaging platform mentioned here.

## Community projects

Community members have brought Crush Monitor to phones and desktops, with a few twists of their own. More projects can be added here over time:

| Project | What's different |
| --- | --- |
| [FQKH / Crush-](https://github.com/FQKH/Crush-) | Android APK with a DeepSeek analysis option. The repository currently provides an APK but not the full source code. |
| [RYANFFY / crush-monitor-pack](https://github.com/RYANFFY/crush-monitor-pack) | Windows and macOS installers that save you from running the setup commands yourself. |

These are unofficial projects maintained independently by community members. Check each repository before downloading, installing or entering an API key; this project does not guarantee their security or functionality.
