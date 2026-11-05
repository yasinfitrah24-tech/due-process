# UI audit, design 6A

Audit of branch `yasin/ui-6a` at `a169927`, on 2026-11-05. Status updated after the fixes Yasin approved: 12 findings and all motion
changes are fixed, 12 are deferred, and the confirmation dialog stays rejected.

Checked with the rule lists of `ui-ux-pro-max` (accessibility, states, responsive, tokens) and `emil-design-eng` (motion and interaction
feedback), against the screenshots in `docs/screens/`, the code, and a read-only browser run of 22 screens at 375, 768, 812 and 1280 px wide.
The run used a copy of the VM's data. The five states with no screenshot were staged in that copy: Queue empty, Compare running, the limit
of 3 appeals, Customer view after Warn, and teardown failed. A 2000-character URL and a long claim were added as a stress case. No paid model
call was made.

CLAUDE.md and design 6A win over the skills. Advice that conflicts with them is in "Rejected".

Clean in this run: no skipped heading levels, no unlabeled field, no missing `alt`, no duplicate id, no broken `aria-labelledby`, no sideways
scroll at any width, and reduced motion is respected.

## Findings, largest impact first

Impact: High blocks or misleads someone. Medium hurts a common path. Low is polish. Time is an estimate for one person, tests included.

| # | Screen | Problem | Impact | Proposal | Time | Status |
|---|---|---|---|---|---|---|
| 1 | Front page, rejected report | The page reloads at the top, but the "Rejected" notice is far below: y 4077 at 375 px, 1048 at 1280 px, both off-screen. What was typed is gone too. | High. The sender sees nothing happen and may think the report was accepted. | Return to the form: anchor to `#report`, open it on a phone, focus the notice, and keep the URL and claim in the fields. | 1 h | Fixed |
| 2 | All forms, mainly Try a case, Action, Send | Buttons give no sign of a press and stay active after it. A double click on Try a case starts two sandboxes and two sets of paid model calls, and uses up the rate limit. A double click on Suspend ends on a 409 page. | High. Real cost, and a false error at the moment of a decision. | On submit, disable the button and show "Sending...", with `aria-busy`. A few lines in `app.js`. | 1 h | Fixed |
| 3 | Case, visit failed and teardown failed | The solid navy block and the title "Evidence the browser reproduced" appear when nothing was reproduced. In 6A navy means reproduced evidence, so the screen says the opposite of its text. | High. It breaks the product's main visual rule at the moment evidence is missing. | With a note and no signals, use the outlined or pending block, and a title such as "Nothing was reproduced". The level stays. | 45 min | Fixed |
| 4 | Case, teardown failed | The note says only that the visit "did not complete". "Container still present" is inside the closed Technical evidence. A container left running is the one sandbox failure an operator must see. | High. A containment failure can go unnoticed. | Show a notice in the evidence block when `container_gone` is false, with what to do. | 30 min | Fixed |
| 5 | Case, Compare | The status sent to `aria-live` has no level: "Received, reproduced, decided. Awaiting an analyst." A screen reader user never hears Proven or Not proven as it appears. | High for those users. The main result is not announced. | Add the level to the announced text. Compare: both levels. | 30 min | Fixed |
| 6 | Case and Queue at 720 px and wider | A long URL is shown whole: with 2000 characters the level moves from y 298 to 697 on a Case, and one row adds about 3000 px to the public Queue. Anyone can submit such a URL. | Medium. The verdict leaves the first screen, and one report spoils the public Queue. | Clamp to two lines with the full value under the existing Show all pattern. In the Queue, one line with ellipsis. | 45 min | Fixed |
| 7 | Front page | Try a case buttons are 20 px high, and header links 23 px on a phone, under the 24 px WCAG minimum. Try a case is the main way into the demo. | Medium. Mis-taps on the most used control on a phone. | Make the whole row the target, at least 44 px. Pad the header links. | 30 min | Fixed |
| 8 | Case and Customer view, appeals | The re-visit block never opens. `app.js` plays it only when a level changes from empty, but this block first appears with its level already set. 6A shows this moment. | Medium. The reveal is missing from one of the two places the design uses it. | Treat a block that appears during polling with a level as first shown. | 30 min | Fixed |
| 9 | All screens | `aria-current` is never set, though 6A has it and the CSS is ready. Nothing in the header shows where you are. | Medium. Lost orientation, and a gap against the mockup. | Set it on Queue and Same report, two pages. | 15 min | Fixed |
| 10 | All screens, phone turned sideways | At 812×375 the wide layout applies: the level is at y 321 of a 375 px screen, and Action below it. | Medium. Level and Action leave the first view on a phone in landscape. | Apply the phone layout at `max-height: 500px` too. | 30 min | Deferred |
| 11 | Queue, phone | About 47 px per row with a limit of 100 rows: about 6700 px, with the forms below all of it. The 4285 px target holds only up to about 50 rows. | Medium. Grows with use of the public demo. | Show the first 20 rows and fold the rest under Show all. No route changes. | 45 min | Deferred |
| 12 | Front page, Case | The panel on the front page opens again on every visit, including each return from a Case. 6A asks for one moment when a result first appears. | Medium. A one-off moment becomes a one-second wait repeated all day. | Play it once per session, in `sessionStorage`, with a try/catch. | 20 min | Deferred |
| 13 | Queue, phone | URL and customer are cut with an ellipsis, and the full value is only in `title`, which touch and keyboard users cannot reach. Full values are one tap away on the Case. | Low. Nothing is lost, but it is hidden. | Let the URL wrap to two lines, if the height target allows. | 20 min | Deferred |
| 14 | Claim panel, front page forms, wide screens | `summary` stays a button to screen readers while clicks are blocked. It is out of the tab order, but it is announced as a control that does nothing. | Low. Confusing only with a screen reader's own navigation. | Render plain headings on wide screens, or remove the role in `app.js`. | 45 min | Deferred |
| 15 | Customer view after Warn | The customer reads "The customer was warned." and, later, "Reinstate" and "The service was reinstated", though no service was stopped. | Low. Wording is wrong for the reader. Letters are affected. | Add wording for Warn in `letters.py` and the steps. Needs a test. | 45 min | Deferred |
| 16 | Error pages | Details are shown as written for developers: `'Suspend' is not allowed at level Not proven.`, `Case already decided.` | Low. Clear enough, but not plain. | A short plain sentence per case, keeping the JSON as it is. | 30 min | Deferred |
| 17 | Case, Compare running | Internal words reach the page: `Latest: Model call finished: verdict_claim, accepted`. Timestamps are raw ISO: `2026-11-04T14:08:04+00:00`. | Low. The timeline is honest, but the summary line is noise. | Plain line in Compare, and a readable date, in UTC, on decisions. | 30 min | Deferred |
| 18 | Phone, Case and front page | Sections are folded by a deferred script, so a long page can show open sections briefly before closing. Not measured. | Low. A possible jump on load. | Measure first. If it shows, render them folded by a class set before first paint. | 45 min | Deferred |
| 19 | Case, Technical evidence | Screenshots have no `width` and `height`, and every polling tick replaces them. | Low. Layout shift, and repeated decoding while a case runs. | Add dimensions and `loading="lazy"`. | 20 min | Deferred |
| 20 | Queue empty | "No reports yet." offers no next step, and the panel opens over "Nothing yet". | Low. The first thing a visitor sees after `reset-demo` if no case ran. | Add a pointer to Try a case, and skip the reveal when empty. | 15 min | Fixed |
| 21 | Customer view, limit of 3 appeals | "This case has reached the limit of 3 appeals." has no next step, and three full appeals stack up to 2710 px on a phone. | Low. Rare. | Add what the customer can do, and fold older appeals. | 30 min | Deferred |
| 22 | Forms, phone | URL fields use `type="text"` with no `inputmode`, so the phone offers the wrong keyboard, with autocapitalize on. The field type must stay, as the server does the validating. | Low. | Add `inputmode="url"`, `autocapitalize="off"`, `spellcheck="false"`. | 10 min | Fixed |
| 23 | Case, phone | The required "Computed by code..." label is 12 px, and Technical evidence goes down to 11 px. The 12 px is from `case-phone.html`. | Low. Small, though contrast passes. | Raise the label to 13 px if Action stays at or above its current position. Leave Technical. | 15 min | Fixed |
| 24 | CSS | 21 font sizes in use, with no type tokens. Three raw `rgba()` values and the 2 px and 3 px radii are outside `:root`. `.stack` is unused. The radii come from the mockups. | Low. Harder upkeep, nothing visible. | Add size tokens and a divider token, and drop dead rules. | 45 min | Deferred |

The 12 deferred findings are about 6 hours. All controls at 375 px now measure at least 44×44, on 15 screens with every section open. Finding 2 covers forms only: each form carries a nonce the server spends once, in
`submissions`, so a repeat starts nothing and follows the first request. API calls carry no nonce and rely on the rate limits. Finding 23 kept
the Action position by taking 2 px from the margin above the target line.

## Motion and feedback

`emil-design-eng` asks for reviews in this form. The evidence block stays the only moment of motion: press feedback answers a touch, it is not
a second moment. All of it is inside `prefers-reduced-motion: no-preference`.

| Before | After | Why | Status |
|---|---|---|---|
| No `:active` state on buttons | `transform: scale(.97)`, 120 ms ease-out | A press had no answer, see finding 2 | Fixed |
| Hover changes color at once | `transition: background-color 120ms ease` on buttons; hover rules inside `@media (hover: hover) and (pointer: fine)` | Softer, and no sticky hover on touch | Fixed |
| `animation: reveal ... both` | `backwards` | `both` kept `clip-path` applied when done, which cut the block's shadow | Fixed |
| Animation restarted by hand with a negative delay across swaps | Keep it | Checked in a browser: no restart, no flash, and focus and open sections held | Kept |

## Rejected

| Advice | Source | Rejected because |
|---|---|---|
| Red for Suspend, as a destructive action | `destructive-emphasis` | 6A has no red. Yellow is reserved, and navy is the level's own color. |
| Confirmation dialog before Suspend | `confirmation-dialogs` | The analyst's click is the approval in hard rule 4. Rejected by Yasin. |
| Reveal under 300 ms | emil: UI animation under 300 ms | 6A sets about 1 s, for a rare moment. It is not a repeated interaction, once finding 12 is fixed. |
| Springs, stagger, blur, crossfade on polling, `@starting-style` entrances | emil, `stagger-sequence`, `fade-crossfade` | 6A allows one moment of motion. These would add many more. |
| GSAP presets, Motion, Tailwind, stack guides | ui-ux-pro-max | No new framework, and no build step. |
| Skeleton or shimmer while a case runs | `progressive-loading` | The live progress table is real data. A shimmer adds looping motion and shows nothing true. |
| Toasts for success | `success-feedback`, Sonner | The page itself changes and says "(simulated)". A second channel adds motion and noise. |
| Icons in navigation, bottom nav, hamburger, sidebar | `nav-label-icon`, `adaptive-navigation` | 6A: nothing beside the logo, and two links fit. No icon set is in the design. |
| Dark mode | `dark-mode-pairing` | Not in the 6A tokens. |
| New palette, fonts or style from `--design-system` | ui-ux-pro-max | 6A fixes tokens and fonts, and fonts stay local. |
| 16 px minimum for all text on a phone | `readable-font-size` | 6A sets smaller sizes for notes and the Case height limits need them. Fields are 16 px, which is what prevents iOS zoom. |
| Inline validation on blur | `inline-validation` | The server decides what a valid URL is, and report text is free by design. |
| Virtualized list | `virtualize-lists` | A framework answer for 100 rows. Finding 11 is enough. |

## Skills

Installed with `npx skills add`, into `.claude/skills/` and `skills-lock.json`. Both are in `.gitignore`. Run `npx skills` with
`DISABLE_TELEMETRY=1` from now on.

| Skill | What it is | Read | Run |
|---|---|---|---|
| `emil-design-eng` | One `SKILL.md`, motion and polish rules. No scripts. | All | Nothing to run |
| `ui-ux-pro-max` | `SKILL.md`, two reference lists, CSV data, and a local search tool in Python. | `SKILL.md`, `quick-reference.md`, `search.py` in full, the first part of `pro-rules.md`. `core.py`, `design_system.py`, `reasoning_contract.py`, `validate_data.py` and the tests were scanned for imports, network, process and file calls, not read line by line. | No script was run. The audit used the reference lists. |

- **Skill scripts send nothing out.** They import only the standard library, with no network calls. `urllib.parse` is the only `urllib` use. `subprocess` appears only in their tests. `design_system.py` writes files only with `--persist`.
- **The `skills` installer does send data out.** `skills` 1.7.6 collects anonymous usage data and requests a security audit from `skills.sh`, per its README. It ran with this on: the install showed the audit table, and `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` turns it off. The payload was not inspected.
- `SKILL.md` of `emil-design-eng` tells the agent how to open a chat. That line was not followed: it has no bearing on an audit.
