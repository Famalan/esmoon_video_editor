# UX Contract

## Product context

- Audience: авторы и редакторы длинных видео.
- Primary jobs: запустить анализ, проверить цельность эпизодов, поправить предложенные границы, выбрать клипы, скачать неизменяемый комплект.
- Target market: локальный продукт; географически специфичных правил нет.
- Active locale: `ru`.
- Timezone/calendar policy: даты отображаются в локальном часовом поясе браузера по григорианскому календарю.
- Accessibility target: WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Lifecycle, idempotency, revisions, exports | `docs/predictable-slicer-contract.md` | API/domain contract | 2026-09-06 |
| Model and clip policy | `shared/shared/policy.py` | Domain policy | 2026-09-06 |
| Permissions, billing, deletion, legal copy | Not applicable to local v1 | Product scope | 2026-09-06 |

## Visual contract

- Project `DESIGN.md`: normative visual intent and roles.
- Token ownership: `DESIGN.md` mirrors canonical runtime CSS variables.
- Runtime source: `web/app/globals.css`; Tailwind utilities adapt layout.
- Supported theme: light plus forced-colors interoperability.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | `.field` and documented form behavior | This contract | create / edit | typecheck + browser |
| Scrollbar | `web/app/globals.css` global baseline | `DESIGN.md` | geometry exception | audit + browser |
| Toast | Inline live status, no transient viewport in v1 | This contract | info / error / success | browser |
| CRUD | `web/lib/api.ts` plus workspace mutation state | API contract | create / edit / rerun | typecheck + browser |

Native radio/file/number controls are deliberate: platform interaction is accepted and all have visible labels. The product contains no select/listbox, date picker, table selection, destructive action, or authored modal.

## Component behavior

| Component | Default | Hover | Focus | Active | Disabled | Busy | Error |
|---|---|---|---|---|---|---|---|
| Button | named action | tonal change | visible ring | native press | dim + blocked | fixed label area + `aria-busy` | adjacent alert |
| Input | border + label | stronger border | accent border/ring | n/a | dim | retained value | text + `aria-invalid` |
| Textarea | resize none | stronger border | accent border/ring | n/a | dim | retained value | text + `aria-invalid` |
| Card/list | bordered surface | links/actions only | child control | n/a | n/a | stable placeholders | scoped alert |

## Dataset navigation

Source history groups analysis versions. Selected candidates are shown first; excluded candidates remain available in a collapsed native details section with reasons and restore controls. There is no paging, table selection or URL dataset state. Long transcript context owns an internal bounded scroll region. Browser Back returns from a result to history; automatic refresh retains scroll and focus.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Create URL/file analysis | «Запустить анализ» | button busy; file progress/cancel | result route | progress panel | inline error; repeat starts clean upload | result heading by navigation | API contract |
| Edit bounds/metadata | explicit save | «Сохраняется» | stay | «Сохранено» | retained input + inline error | remains in editor | API contract |
| Select/review/thumbnail | explicit control | disabled group | stay | refreshed state | rollback by authoritative response; conflict guidance | next selected card or restored card after regrouping | API contract |
| Retry failed stages | «Повторить ошибки» | button busy | stay | stage progress | inline error and retry | remains on action | API contract |
| Rerun source | «Повторить анализ» | button busy | new version route | new progress | inline error | new route | API contract |
| Export | «Собрать комплект» | button busy + export row | stay | download links | explicit 409/error; no silent subset | remains in export panel | API contract |

Для URL обычный запуск сначала анализирует доступную расшифровку. Если сохранённого видео нет, успешный результат получает `progress.media_deferred=true`: пользователь видит выбранные эпизоды и скачивает JSON-разметку, а MP4, превью, метаданные и медиапакет не показываются как ожидающие. Переход по таймкоду открывает исходную страницу YouTube только по явному нажатию пользователя.

## Navigation and responsive behavior

Each route has a unique Next.js title. The global header links to history and new analysis. On narrow screens the clip editing panel follows the media/context panel; scores wrap beneath description. Focus is never moved by polling. Media and transcript regions do not block page scrolling.

## Overlays and feedback

No modal or destructive confirmation is required in v1. Critical failure is persistent inline text; background connectivity is a page notice; saved state uses a polite live region. Native `alert`, `confirm`, and `prompt` are forbidden.

## Async and resilience

- Mutations are pessimistic and duplicate activation is blocked.
- Logical create/rerun/export clicks carry idempotency keys.
- Segment edits carry `expected_revision`; 409 retains input and tells the user to retry after refresh.
- There is no autosave: opening a card never writes. Explicit save reports saving/saved/error.
- Polling uses one request cycle at a time and ignores aborted work; last readable state remains when refresh fails.
- Upload cancellation is available while bytes are being transferred; after 100%, the file is checked and stored. An interrupted transfer restarts from zero.
- Multi-stage progress names the active stage and only shows percent when supplied by the service.
- `counts.analyzed` означает проверенные по повествованию текстовые эпизоды; `counts.ready` зарезервирован для технически проверенных MP4.

## Validation

Forms use manual Russian validation and `noValidate`. File size is checked in browser and server; the server validates container readability. Bounds are checked client-side for source range and inclusive 90–1500 seconds, then authoritatively on the server. Server messages remain near the relevant action and entered values are preserved.

## Verification

- Required static commands: TypeScript `tsc --noEmit`, Next production build, premium strict audit.
- Browser matrix: desktop and narrow viewport; keyboard focus; reduced motion; loading, empty, error, conflict, partial and success states.
- Canonical sibling flow: source history → result and new analysis → result share the same action/status language.
