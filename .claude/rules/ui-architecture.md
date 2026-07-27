# UI Architecture Rules — Object-Centric Workspace

> **Scope:** Enforces single-page, context-centric UI patterns for the object-centric workspace
> **Applies to files:** `frontend/src/components/**/*.tsx, frontend/src/app/**/*.tsx`

## Intent

The workspace is one persistent stage: users stay on the graph while acting on **objects** (classes, edges, properties, documents, ontologies, runs, pipeline steps). Features are added as **capabilities on those objects**, not as new destinations.

Two failure modes must never happen:

1. The user asks *"where am I?"* (navigation disorientation).
2. The user asks *"what does this color/size mean?"* with no answer available in-UI (encoding ambiguity).

---

## Core Principles

### 0. LEFT-CLICK SELECTS, RIGHT-CLICK ACTS.

This is the interaction contract behind every other rule.

| Gesture | Meaning | Surface |
| --- | --- | --- |
| **Left-click** on an entity | Select + open read-only detail panel | `FloatingDetailPanel` (canvas entities) or `AssetInfoPanel` (explorer assets) |
| **Right-click** on an entity or canvas | Open context menu of **actions** | `ContextMenu` |
| **Drag** between explorer and canvas | Initiate extraction / reparenting / import | Drop zones |
| **Keyboard accelerator** (1–5, Esc, etc.) | Shortcut for the most frequent menu items | `window` key listener when focus is not in an input |

**Never** attach mutation actions to left-click (no "click to approve", no "click to delete"). Read-only selection must be safe.

### 1. NO NEW ROUTES FOR WORKSPACE WORKFLOWS.

All new object-centric work integrates into `/workspace`.

- **Exempt routes:** `/login`, `/logout`, `/api/*` route handlers (health, ready), and any framework-mandated auth/error shells.
- **Legacy routes** (`/curation`, `/dashboard`, `/library`, `/ontology/[id]`, `/pipeline`, `/quality`, `/upload`, `/entity-resolution`) existed before this rule. Do not link to them from new code, do not extend them with new features, and plan migration to overlays. Removing a legacy route is preferred over growing one.
- **Deep-linking is done via query params on `/workspace`**, read by `useSearchParams()` — e.g. `?ontologyId=…&runId=…&lens=confidence`. This preserves shareable URLs without introducing routes.

### 2. NO ACTION BUTTON PANELS AS THE PRIMARY PATH.

Entity operations are initiated from **right-click context menus**. Toolbars and side panels may *duplicate* a subset for discoverability, but must not be the *only* path.

### 3. CONTEXT OVER NAVIGATION.

Prefer updating selection + a floating panel over a new page. Use overlays, lenses, and context menus instead of routing.

### 4. CANVAS CONTENT IS OBJECT-DRIVEN, NOT MODE-DRIVEN.

The center pane renders whatever matches the **primary selected object**:

| Primary object | Canvas content |
| --- | --- |
| Ontology | Graph canvas (Sigma/graphology or React Flow) |
| Run | Pipeline DAG + run metrics |
| (none) | `EmptyCanvasState` |

Swapping between these is an **object swap, not navigation**. A global "edit mode" vs "view mode" is forbidden.

### 5. DRAG AND DROP OVER MULTI-STEP WIZARDS.

Prefer DnD (document → canvas for extraction, class → class for reparenting, ontology → ontology for imports) over page wizards that imply navigation.

### 6. LENS / GRAPH STYLE / LAYOUT ARE THREE DIFFERENT AXES.

All three live in the canvas context menu. None of them belong in a toolbar as the primary path.

| Axis | What it changes | May relayout? | How to change |
| --- | --- | --- | --- |
| **Lens** | Paint attributes (color, ring, size from the same `baseSize`) on the stable graph | **Never** | Canvas menu → "View As" submenu + keyboard 1–5 |
| **Graph style** | Node/edge geometry (circle vs UML box, straight vs curved edge) | Sometimes — geometry may force relayout | Canvas menu → "Graph Style" submenu |
| **Layout** | Node *positions* (force / circular / grid / random) | Always — that's its job | Canvas menu → "Layout" submenu |

The active lens is shown in a **subtle header indicator** (e.g. "(Semantic view)" in `LensToolbar`), never as a competing top-level switcher.

### 7. RICH, ENTITY-SPECIFIC CONTEXT MENUS.

Every on-canvas and explorer entity has its own menu. Extend `workspace/contextMenus/<entity>.ts` (see Principle 13) — do not extend a single giant switch in `page.tsx`.

Currently shipping menus:

- **Class node:** View Details, Approve/Reject, View Version History, View Provenance, Delete.
- **Edge:** View details, Approve/Reject (when curation API supports it), Delete (when supported).
- **Property:** View, Approve/Reject, Copy URI.
- **Canvas:** View As, Graph Style, Layout, Edge Style, Fit All, Center View, New Ontology.
- **Document (explorer):** View Info, Delete (plus Extract / View Chunks / Rename when implemented).
- **Ontology (explorer):** Open in Canvas, View Info, Edit Name & Description, Release, Manage Imports, View Quality Report, Export, Delete.
- **Run (explorer):** View Pipeline & Metrics, Copy Run ID, View Run Info, View Extracted Entities, Retry, Delete.
- **Pipeline step:** View Step Details, Copy Error, View Run Results, Retry Run.
- **Pipeline canvas:** Fit All, Center View, Copy Run ID, View Run Info, View Extracted Entities, Retry, Delete Run.

### 8. PERSISTENT ZONES — RESIZABLE, NEVER COLLAPSED.

The `/workspace` layout has three zones that *always exist* when relevant:

- **Left:** Asset explorer (documents, ontologies, runs).
- **Center:** Canvas (graph or pipeline DAG per Principle 4).
- **Bottom:** VCR / timeline when an ontology is open.

Zones are **resizable** (explorer width, pipeline split pane), never toggleable to zero. Do not add "hide sidebar" buttons — users tune widths, they don't flip visibility.

### 9. OVERLAY PANELS, NOT PAGES.

Class/edge/run/ontology/provenance/quality details use slide-in or floating panels over the canvas, never a new route. Dialogs (`OntologyRenameDialog`, `OntologyReleaseDialog`, `CreateOntologyDialog`, `ManageImportsOverlay`, `QualityReportOverlay`) render as children of the workspace page and are opened from context-menu actions.

### 10. SIMULTANEOUS PANEL PLACEMENT MUST BE COORDINATED.

When two panels can be open at once (e.g. `FloatingDetailPanel` + `AssetInfoPanel`), they must use **distinct default placements** so they don't spawn on top of each other.

- Use `useDraggablePanel(width, { placement })`.
- Available placements: `viewportTopRight` (entity details), `mainColumnTopLeft` (asset info).
- Same-placement overlays must use `stackIndex` (0, 1, 2, …) to diagonally offset.
- Panels are always draggable by their header grip (`PanelDragGrip`) and always dismissable with Esc or `×`.

### 11. LINKS ARE ALLOWED FOR NAVIGATION OUT OF THE WORKSPACE ONLY.

A `<Link>` to `/`, `/login`, or `/logout` is fine. A `<Link>` that initiates an object-centric workflow (open ontology, view run, review class) is forbidden — use a context-menu action or URL param deep link.

---

## Graph Lenses, Visual Encoding & Legend

### 12. EVERY ENCODING IS LEGIBLE IN-UI.

For each lens, `CanvasLensLegend` must document:

- What **node color** and **node border/ring** mean.
- What **edge color** and **edge weight** mean (when used).
- What **node size** means — explicitly. If size is **structural** (PageRank, degree), say so. If a lens *intentionally* ties size to another attribute (e.g. Confidence lens may scale by score), call that out.
- Any **fallback chain** for missing fields (e.g. "per-class tier when present; otherwise ontology's library tier; grey = neither").

### 13. NO IMPLICIT METAPHORS.

Users will assume "big = important to review" or "bright = bad" unless the legend contradicts it. Spell out every convention.

### 14. LENS CHANGE = PAINT, NEVER RELAYOUT.

A lens change must preserve the graph's **topology fingerprint** (count + ids of nodes and edges) and its layout positions. Paint color/size on the existing graph; do not rebuild it.

This is testable: before/after a lens change, the node key set and edge key set must be equal, and positions of nodes that existed both before and after must be unchanged. Add this assertion to any new canvas implementation's tests.

Topology changes (timeline-filtered subgraphs, new extraction results) **may** relayout — that's a data change, not a lens change. The legend should reflect which case is active.

### 15. TEMPORAL / DIFF LENS IS THE EXCEPTION THAT PROVES RULE 14.

Scrubbing the VCR timeline can change which entities exist → layout may change. The legend must distinguish "lens change (stable layout)" from "scrub (different subgraph)" so users don't conflate them.

---

## Objects & Data Parity

### 16. EDGES ARE FIRST-CLASS.

Selection, detail panel, context actions, API mutations, and legend rules that apply to nodes apply to edges. Do not ship class curation without a plan for edge curation when edges carry review state.

### 17. OPTIMISTIC UI FOR CURATION, VIA A SHARED HELPER.

Approve/reject from the menu updates local graph state immediately; on API failure, roll back (or refresh). Do **not** reimplement this per entity kind in `page.tsx`. Use (or create) a shared helper — e.g. a hook that takes `{ entityKind, ontologyId, onRollback }` and returns `{ approve, reject }`.

### 18. DESTRUCTIVE ACTIONS — NEVER NATIVE DIALOGS.

- **Forbidden:** `window.confirm`, `window.alert`, `window.prompt`. Anywhere. No exceptions.
- **Reversible destructive** (delete class, reject, remove import): act immediately with `danger: true` styling in the menu + show an **undo toast**. Undo-over-confirm is the default.
- **Irreversible destructive** (delete ontology, release a version, delete run): use a dedicated confirmation **overlay** (modal component with typed-name or explicit Confirm button), not a `confirm()`.
- Consistency is required — if delete-class needs confirmation, so does delete-edge and delete-ontology.

---

## Discoverability & A11y

### 19. KEYBOARD ACCELERATORS ACCELERATE; THEY DO NOT REPLACE THE MENU.

Every shortcut must also be reachable from a context menu. Shortcuts are hints, not primary UX. Existing accelerators: `1`–`5` (lens), `Esc` (close menu/panel). Document new ones in a help surface (tooltip, "?" overlay) rather than a permanent key-legend panel.

### 20. CONTEXT-MENU-PRIMARY IS HARD TO DISCOVER — MITIGATE EXPLICITLY.

Allowed mitigations (all of these can coexist):

- One-line empty states: e.g. `EmptyCanvasState` says "Right-click on canvas for more options."
- Legend copy that tells users to right-click.
- First-run toast or tour.
- A single discreet "?" help overlay (kept out of the primary action path).

A permanent wall of toolbar buttons duplicating every action is **not** allowed — it trains users to ignore the menu.

---

## Engineering Patterns

### 21. CONTEXT-MENU BUILDERS ARE COLOCATED BY ENTITY.

`getContextMenuItems()` in `page.tsx` is a historical single switch. Going forward:

- Each entity type's menu builder lives in `frontend/src/components/workspace/contextMenus/<entity>.ts` and exports a function `build<Entity>ContextMenu(args): ContextMenuItem[]`.
- The workspace page wires the selected type to the matching builder, passing handlers (approve, reject, refresh, etc.) as arguments.
- When a new entity type is added, create its builder in that folder — do not extend the switch in `page.tsx`.

This keeps `page.tsx` under the 1500-line cap from `modularity-and-structure.mdc`.

### 22. EVERY NEW ENTITY TYPE MUST SHIP WITH THIS CHECKLIST.

| Required | Where it lives |
| --- | --- |
| Left-click selection handler | `page.tsx` (or wherever the canvas/explorer is wired) |
| Read-only detail panel | `FloatingDetailPanel` extension or a dedicated panel component |
| Right-click context menu builder | `workspace/contextMenus/<entity>.ts` |
| Legend entry for every lens in which it's visible | `CanvasLensLegend` |
| Optimistic curation (if curable) | Shared helper — not duplicated |
| Test: menu items render and fire handlers | `__tests__/` next to source |

Shipping an entity without all six rows is an incomplete feature per `comprehensiveness-over-simplification.mdc`.

### 23. CANONICAL ICONS PER ACTION VERB.

To keep menus readable at a glance, reuse the icons already in the codebase. Do not invent a new icon for a verb that already has one.

| Verb | Icon |
| --- | --- |
| View / inspect | 🔍 |
| View info | ℹ️ |
| View history | 📜 |
| View provenance / imports | 🔗 |
| View data / report | 📊 |
| Approve | ✅ |
| Reject | ❌ |
| Delete | 🗑️ |
| Copy | 📋 |
| Edit / rename | ✏️ |
| Retry | 🔄 |
| Open in canvas | 🔷 |
| Pipeline / metrics | ⚡ |
| Export | 📤 |
| Release / publish | 🚀 |
| Fit / frame | ⬜ |
| Center | 🎯 |
| Layout | 🔄 *(acceptable — context disambiguates from "retry")* |
| Edge style | 〰 |
| Graph style | 📐 |
| View As (lens) | 👁 |
| Add / new | ➕ |

Submenus collapse into a right-arrow `▸`; checkmarked items in a radio-style submenu show `✓` in place of the icon (`ContextMenu.tsx` handles this automatically).

---

## Anti-Patterns (❌)

- New Next.js **routes** for workspace workflows that could be overlays.
- `<Link>` as the primary path for canvas-adjacent tasks.
- Multi-step **page** wizards for actions that could be DnD + panel.
- **Toolbar-only** actions for graph entities (no context-menu path).
- Left-click triggering a mutation (approve, delete, reject).
- Separate global "edit mode" vs "view mode" for the canvas.
- Side panel that **replaces** the canvas instead of sitting beside/over it.
- Lens change that **re-runs layout** without a topology change.
- Colors/sizes that **mean different things** in different lenses **without** legend text calling it out.
- `window.confirm` / `alert` / `prompt` anywhere in the UI.
- A growing single-switch context-menu builder in `page.tsx`.
- Two overlay panels that spawn at the same placement without `stackIndex` offset.
- Inventing a new icon for an action verb that already has a canonical one.

## Preferred Patterns (✅)

- Context menus on node, edge, property, canvas, and explorer items.
- Per-entity context-menu builders in `workspace/contextMenus/`.
- Keyboard accelerators for lens and viewport.
- Header / corner **status** for active lens and ontology name (not a duplicate primary switcher).
- **Lens legend** anchored near the canvas (e.g. bottom-left), updated per `LensType`.
- Drag-and-drop between explorer and canvas.
- Inline edit where safe (e.g. rename).
- Floating detail panels with minimize/dismiss, distinct placements, and `stackIndex` when stacked.
- Query-param deep links on `/workspace`.
- In-app confirmation overlays for irreversible ops; undo toasts for reversible ones.
- Toasts for async success/failure.
