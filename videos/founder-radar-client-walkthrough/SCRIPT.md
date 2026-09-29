# Founder Radar client walkthrough

## 00:00:00,000 — Your morning, in one place

Welcome to Founder Radar. This short walkthrough shows where the companies come from, how the fund matches are calculated, and how to change the settings. Your daily job starts on Today. You review companies, rather than a pile of articles. Kept holds the companies you want to revisit. The names and example facts are teaching data. The completed checks in this copy are simulated. We are not changing your live list, your decisions, or your Google Sheet.

## 00:00:25,028 — Two tracks. Different jobs.

Track A looks for useful signals. These can be university spinouts, accelerator companies, grant awards, or a specific technology news story. Track B uses Companies House, the official company register. It helps verify the company identity and incorporation date. A newly registered company is not automatically a promising startup. Registration is also not proof of when trading began, or proof that no investment has happened. Missing facts stay unknown until there is evidence.

## 00:00:52,923 — How a clue becomes a company card

Here is the whole journey. A source provides a clue. The system checks which company it belongs to, combines duplicate sightings, and keeps the supporting links. It turns longer text into small facts, such as location, business type, stage, and funding evidence. Next come the fund rules and the scores. This calculation uses code, not a chat model. The same facts and settings give the same result. Finally, Hermes checks the proposed company before publication. This last check can withhold a bad match. It cannot invent another company or change the calculated scores.

## 00:01:25,332 — Read Today without guessing

On Today, start with the company name and the short description. Check where it is based, the evidence about its age, and the suggested fund. Match means how closely the known facts fit that fund's preferences. Fresh means the discovery opportunity, using the evidence available about age, tracked coverage, funding, and how it was found. A high Fresh score is not proof that nobody knows the company. Coverage tells you how much evidence supports the match. If a field says unknown, treat it as a gap, rather than silently filling it in.

## 00:01:54,018 — Four funds. Keep the real rules.

The four fund groups are Northstar, DSW, Outward, and Anticus. Northstar has regional and specialist vehicles, with the North East especially relevant. DSW has separate SEIS and EIS paths. Outward looks at early technology serving complex industries. Anticus has Yorkshire regional vehicles. London remains eligible for the softer DSW EIS regional rule. When another suitable fund matches, Today can suggest that alternative and explain the regional preference. If none does, it shows the weaker regional match honestly.

## 00:02:25,108 — Understand the calculation

Let us use one fictional company, River Finance. Our example says it is a twelve month old UK fintech company, with pilot customers and two million pounds of disclosed funding. The current default calculation gives Outward a Match of sixty one point seven, and Fresh of sixty five point five. Priority combines these: sixty percent Match, plus forty percent Fresh. That gives sixty three point two after rounding. These numbers describe the example under these settings. Hard rules decide eligibility first. An attractive score cannot rescue a company that fails an applicable hard rule.

## 00:02:58,562 — Change a source safely

The Google Sheet is the durable place for configuration: sources, fund criteria, scoring weights, and general settings. Here we use a local replica, clearly marked demo. In Sources, we switch northern accelerator from enabled to disabled, then save the demo settings. This really updates the source configuration. A future collection run will skip that source. It does not erase companies already stored, and it does not automatically lower their scores. For the live system, make a small deliberate edit, check that configuration loads, and then run the appropriate update flow.

## 00:03:31,010 — Change a genuine fund rule

Now we edit a real field from the default Outward fund row: prior total max, the ceiling used for previously raised capital. The default row contains twenty million pounds. For teaching only, we tighten it to one million. This is not a claim that Outward changed its mandate. River Finance has two million of previous funding in our example, so it now fails this vehicle's hard rule. Rescoring returns reject, with no eligible vehicle. In real use, only change a fund rule when the fund's current criteria support that change, and keep the evidence for it.

## 00:04:02,279 — Thresholds and weights do different things

The shortlist threshold controls the label. Our default Match threshold is seventy. Lowering it to sixty puts River Finance on the shortlist because the example also meets the Fresh and coverage conditions. Match stays sixty one point seven. Fresh stays sixty five point five. We changed the threshold, not the facts or their scores. Next we restore the threshold and change Priority to eighty percent Match and twenty percent Fresh. Priority becomes sixty two point five. The two underlying scores still stay the same. Weights control emphasis. Thresholds control which bucket a result enters.

## 00:04:36,212 — Save → rescore → final check → publish

After a live settings change, load the updated configuration and rescore the stored companies. The system records which configuration produced each score, so old results can be identified. Hermes must then complete the final company checks for the updated candidates. A successful calculation alone does not approve publication. If the final check is unavailable or incomplete, the company waits. It stays off Today, the published Today sheet, and the digest until it passes. This is useful protection: a quiet morning is better than presenting an unchecked list as ready.

## 00:05:07,978 — Ready and waiting are separate

The counter separates ready to review from awaiting final check. In our demo, two companies are ready and one is waiting. The ready count reflects the remaining review backlog, rather than resetting to the number on one page. Today also aims for a useful mix of qualified sources, funds, and sectors. This is soft ordering, not a compulsory equal quota. A company does not get fake points to make the mix look balanced. Quality and the real fund rules still come first.

## 00:05:34,155 — Keep your decisions in the same store

Three buttons finish the morning review. Worth contacting saves the company to Kept. Unsure also saves it there for later. Not for me stores the rejection, and that company does not return to Today. Here we actually save one decision in the isolated copy, then open Kept. The application database, called SQLite, is the main store for these choices. The Sheet can mirror the verdicts when configured, and a full synchronization catches up missed writes. Phone actions must call the same saved decision command. A chat reply alone is not a saved choice.

## 00:06:05,830 — A simple daily routine

For daily use, open Today, check the explanation and evidence, then choose yes, maybe, or no. Return to Kept when you are ready to contact companies. Change settings in the Sheet when you have a clear reason, then recalculate and complete the final checks before publishing again. Use the teaching guide for the Easy explanation or the Technical detail. This walkthrough used demonstration data throughout. Your live settings and decisions were untouched. The goal is a small, explainable UK company list that helps you make the next decision, while leaving unknowns and unfinished checks visible.
