# Reddit Annotation Guidelines

These labels are draft labels for a Singapore Reddit language dataset. The local LLM can help with first-pass extraction, but every label must be checked by a human before it becomes final.

## Label Categories

### names

Use `names` for named entities that appear in the comment text, including:

- Person names and public figures.
- Organisations, companies, political parties, agencies, schools, and teams.
- Platforms, apps, websites, news sources, and named communities.
- Usernames only when the username is explicitly written inside the comment body.

Do not label the comment author metadata as a name. Do not infer a name that is not present in the text.

### locations

Use `locations` for Singapore-specific places or address-like references that appear in the comment text, including:

- Neighbourhoods, estates, towns, and local abbreviations such as `CCK` or `AMK`.
- Roads, expressways, MRT stations, malls, buildings, schools, parks, and landmarks.
- Address-like references such as `BLK 81`.

Preserve abbreviations exactly as written. Do not expand `CCK` to `Choa Chu Kang` unless the expanded text also appears in the comment.

### singlish

Use `singlish` for Singlish words and phrases that appear in the comment text, including:

- Particles such as `lah`, `leh`, `lor`, `sia`, `meh`, `ah`, and `liao`.
- Local phrases such as `blur sotong`, `where got`, `can meh`, `wa lao eh` and `really ah`.
- Local vocabulary such as `sian`, `shiok`, `atas`, and `sibeh`.

Label meaningful phrases as full phrases where appropriate. For simple particles, label the particle exactly as written.

## General Rules

- Extract only text that appears in the comment.
- Preserve the spelling, casing, and abbreviation used in the comment.
- Do not add explanations, normalisations, translations, or inferred labels.
- If a quoted sentence contains a label-worthy term, label it only if the term appears in the quoted text.
- If uncertain, leave the item out or flag it for human review outside the JSON label arrays.
- Empty comments, deleted comments, removed comments, URL-only comments, and GIF-only comments should normally remain unlabeled.

## Human Review

LLM output is not final ground truth. It can overclassify ordinary Singapore-related wording as Singlish, miss subtle local phrases, or infer entities that are not actually present. Reviewers should compare each draft label against the original comment text and remove anything that does not exactly appear or does not fit the category.
