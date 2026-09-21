# Interactive Lesson Builder (V31.108)

Incremental feature on the existing Flask + Firebase + vanilla-JS app. No new
application, no framework change, no existing route contract changed.

## Data model
`lessons/{id}` (existing collection) gains optional fields:
`blocks[]` = `{id, type, order, content}`, `blockSchemaVersion`, `blockCount`,
and `contentType: "blocks"` for lessons created by the builder. A lesson
without `blocks` behaves exactly as before.

`lessonBlockKeys/{lessonId}` (server only): `{blocks: {blockId: {answers, explanations}}}`.
`lessonBlockResults/{studentUid}_{lessonId}_{blockId}` (server only): attempts and scores.
Both are `allow read, write: if false` in `firestore.rules`.

Render data (embed URLs, provider, flags) is derived from stored content on every
read by `lesson_blocks.render_blocks()`; it is never stored and never trusted.

## Block types
| type | stored content | student render |
|---|---|---|
| text | plain text; HTML tags/attributes stripped (comparisons like `a<b and c>d` survive) | paragraphs via textContent |
| formula | LaTeX (no links/macros/HTML) | KaTeX via `math-render.js` |
| image | https image link, or Canva *view* link | `<img>` / sandboxed Canva embed |
| video | YouTube, Vimeo, Drive, Cloudinary, direct .mp4/.webm | fixed-host embed or `<video>` |
| pdf | https link + title | outbound link card |
| geogebra | **material id only** | sandboxed `geogebra.org/material/iframe/id/<id>` |
| simulation | https link + title | PhET `/sims/*.html` embedded; every other link is outbound-only |
| practice | questions; key server-side | unlimited tries, key shown after checking |
| quiz | questions; key server-side; attempts 1-10 | scored; key shown only when no attempts remain |
| assignment | id of the teacher's own assignment for the course grade | existing assignment submit flow |

Canva is not recreated: use an exported image or a Canva "View" link.
PhET-iO is **not** integrated; simulations are plain iframes/links.

## Routes (`lesson_builder_routes.py`)
Teacher (approved, owns course + lesson): `GET /api/teacher/lesson-blocks/types`,
`POST /api/teacher/courses/<id>/block-lessons`, `GET|PUT /api/teacher/lessons/<id>/blocks`,
`POST /api/teacher/lessons/<id>/blocks/reorder`.
Student: `GET /api/student/lessons/<id>`, `POST /api/student/lessons/<id>/blocks/<blockId>/answers`.
Access rules = existing lesson-completion rules (own grade exactly, published course,
lesson and unit, Learning Mode/Audience of the course via `targeting_access`).
Lessons inherit targeting from their course; there is no per-lesson targeting.

## Adding a block type
Subclass `lesson_blocks.BlockType` (`clean`, `render`, optional `references`),
`register_block_type()` it, add a client renderer with
`BMTLessonBlocks.registerRenderer(type, fn)`. Routes and storage need no change.

## Known limits
- Quiz attempts are counted inside a Firestore transaction. The in-memory test fake has no
  real contention, so the tests check the wiring (read + write through one transaction), not a race.
- A bare, known HTML tag written in prose (`<b>`, `<i>`) is removed; put maths in a Formula block.
- Embeds (GeoGebra/PhET/YouTube/Canva) were not exercised against the live sites in the
  build sandbox (no network): URL construction, allow-listing and sandbox attributes are tested,
  and the renderer/builder were driven in headless Chromium against the real routes with the
  embed hosts unreachable.
- KaTeX rendering of Formula blocks was not exercised in the browser test (KaTeX is loaded from a CDN).
- The Firestore rule change was syntax-reviewed only; run it in the emulator before deploying.
