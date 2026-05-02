# 07. Asset Pipeline

Здесь — спека формата ассетов **и готовые промпты на английском** для генерации (Midjourney / DALL·E / ChatGPT image / Imagen 3 / Stable Diffusion).

> **Главное правило**: всегда добавляй "Style Anchor" (см. ниже) в начало промпта. Это держит визуальный стиль одинаковым через всю игру. Без него у тебя получится зоопарк из разных стилей и игра будет выглядеть как набор картинок из интернета.

---

## 1. Базовая спека (соблюдать ВСЕГДА)

### 1.1 Перспектива
- **Top-down 3/4 view, ~30° angle from above** (как в Clash of Clans / Hay Day / Township)
- НЕ классическая 2:1 изометрия
- НЕ строго плоский top-down
- Камера смотрит с верхне-передней стороны

### 1.2 Размеры
| Сущность | Размер PNG |
|---|---|
| 1×1 building (хижина, колодец) | **256×256** |
| 2×2 building (лесопилка, ферма, склад) | **512×512** |
| 3×3 building (Town Hall, рынок) | **768×768** |
| 4×4 building (монументы) | **1024×1024** |
| Тайл местности | **128×128** |
| Декорация (дерево/камень/куст) | **128×128 — 256×256** |
| Иконка ресурса | **128×128** (отображается 32-64) |
| UI кнопка | **256×128** или **128×128** |

> Генерируем **в 2 раза больше** чем будет на экране — для сглаживания при downscale. Phaser сам отрисует в нужном размере, но исходник высокого качества даёт чёткий результат на retina-экранах.

### 1.3 Anchor / якорь
- **Все здания**: anchor = **(0.5, 1.0)** = bottom-center
  Здание "стоит" нижним краем PNG на тайле. Высота PNG может превышать ширину (выпирающие крыши/башенки).
- **Иконки**: anchor = **(0.5, 0.5)** = center
- **Тайлы местности**: anchor = **(0.5, 0.5)** = center

### 1.4 Фон и тени
- **Фон полностью прозрачный** (PNG with alpha channel, NO white/colored background)
- **Тень под зданием — да**, мягкая эллиптическая, **запечена в PNG** (для MVP так проще)
- Без UI-рамок, без подписей, без текста на изображении

### 1.5 Цветовая база
- Насыщенные, тёплые, "конфетные" цвета
- Контрастная **тёмная обводка** ~3-5 px (как у CoC)
- Тёплые тона для жилых, холодные для индустриальных, золото для премиум
- Палитра — см. [08_visual_language.md](08_visual_language.md)

### 1.6 Технические требования к файлу
- Формат: **PNG-32 (RGBA)**
- Никакого JPEG (нет альфы, артефакты)
- После генерации: прогнать через `optipng` (без потерь, экономит 20-50% веса)
- Имя: строго по конвенции из секции 2

### 1.7 Naming convention

```
building_<id>_lvl<N>.png            # building_lumbermill_lvl1.png
building_<id>_construction.png      # один на все уровни — здание в стройке
res_<id>.png                        # res_wood.png
tile_<type>_<n>.png                 # tile_grass_1.png, tile_grass_2.png
deco_<type>_<n>.png                 # deco_tree_oak.png
ui_<element>.png                    # ui_button_build.png
icon_<id>.png                       # icon_quest_first_lumbermill.png
npc_<id>.png                        # npc_villager_default.png
```

Только нижний регистр, snake_case, без пробелов и кириллицы в именах файлов.

### 1.8 Структура папок

```
village-tycoon/public/assets/
├── buildings/                  # 30+ PNG в MVP
├── resources/                  # 6 PNG в MVP
├── tiles/                      # 6 PNG в MVP
├── decorations/                # 7 PNG в MVP
├── ui/                         # ~20 PNG в MVP
├── icons/                      # квесты, технологии (Beta)
├── npc/                        # жители, юниты (Beta)
├── effects/                    # частицы, искры (Beta)
└── atlases/                    # сгенерированные атласы (TexturePacker output)
```

---

## 2. Style Anchor (уже встроен в каждый промпт ниже)

Все промпты в секциях 3-7 **уже содержат** этот стилевой якорь в начале — копируй блок как есть, не нужно ничего добавлять.

Это для справки + для собственных промптов когда расширяешь:

**Стандартный якорь** (для зданий, тайлов, декораций, иконок ресурсов, NPC):

```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.
```

**Tile-вариант** (для бесшовных тайлов местности — без прозрачного фона, без объекта):

```
A 2D mobile game seamless tile texture in the style of Clash of Clans and Hay Day,
top-down 30 degree view (matching the game perspective),
vibrant cartoon art style, saturated warm colors, soft cel-shading,
flat ground texture with no objects dominating, no shadows from objects,
edges must seamlessly tile with itself when placed in a grid,
opaque background (NOT transparent — this is ground),
square 1:1 aspect ratio, no UI elements, no labels, no text,
professional mobile game quality.
```

**UI-вариант** (для кнопок, панелей, фреймов — без 30° угла):

```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.
```

**Negative prompt** (если используешь Stable Diffusion / MJ `--no`):
```
realistic, photorealistic, 3D render, low quality, blurry,
watermark, signature, text, UI, frame, border, white background,
gradient background, shadow on wall, multiple objects, jpeg artifacts,
sketchy, hand-drawn pencil, anime, manga, dark gritty
```

---

## 3. Промпты для MVP-зданий (8 типов × 3 уровня)

> На каждое здание — 3 промпта (lvl 1 / 2 / 3). Каждый уровень должен выглядеть как **эволюция того же здания**, не как разные здания. Чтобы это держалось — упоминаю "evolved version of the previous level" в lvl 2 и lvl 3.

### 3.1 Town Hall (Ратуша) — 3×3, 768×768

**`building_townhall_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval village town hall, level 1.
Wooden log cabin construction with thatched straw roof,
a small central tower with a wooden flagpole and red triangular flag,
two small shutter windows, simple wooden double-door entrance,
modest peasant village vibe, warm brown wood tones,
sitting on a small grassy circular platform.
3x3 tile size building.
```

**`building_townhall_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval town hall, level 2 — evolved upgrade from a wooden log cabin.
Stone foundation with wooden upper walls, red clay tile roof,
a taller central watchtower with golden spire and proud red banner,
larger arched windows with iron lattice,
double oak doors with metal studs, two side wings extending out,
flowering window boxes for color,
sitting on a stone-paved circular platform with a small garden.
3x3 tile size building.
```

**`building_townhall_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A grand medieval city hall palace, level 3 — heavily upgraded prestigious version.
Polished white marble walls with golden trim,
a tall central golden dome with a crown spire on top,
multiple smaller golden cupolas on side towers,
ornate columned entrance facade with grand staircase,
majestic banners on each tower flying in the wind,
gem-encrusted decorative carvings, royal red carpet leading inside,
sitting on a wide marble platform surrounded by hedges.
3x3 tile size building, looks rich and powerful.
```

### 3.2 Lumbermill (Лесопилка) — 2×2, 512×512

**`building_lumbermill_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple medieval lumber mill, level 1.
Small open-air wooden sawmill shed,
roughly cut wooden logs piled beside it,
a single hand saw on a wooden bench, axe stuck in a stump,
sawdust scattered around the base,
thatched roof on wooden support poles,
warm brown wood tones, surrounded by a few small bushes,
sitting on a grassy 2x2 plot.
```

**`building_lumbermill_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval water-powered lumber mill, level 2 — upgraded from a basic sawmill.
A wooden building with a stone foundation, sloped wooden plank roof,
a large wooden water wheel attached to one side rotating in a small stream,
neatly stacked piles of milled wooden planks,
chimney with light smoke wisp, small workshop window with warm glow,
sitting on a grassy 2x2 plot with a small flowing stream.
```

**`building_lumbermill_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

An industrial-era large lumber mill, level 3 — heavily upgraded operation.
Multi-story wooden building with red tile roof,
two large water wheels powering internal mechanisms visible through openings,
multiple stacks of cut planks and treated logs around the perimeter,
a horse-drawn cart loaded with logs parked nearby,
tall brick chimney emitting wispy smoke, lanterns hanging on the walls,
sturdy stone foundation with cobblestone access path,
sitting on a 2x2 grass plot with a bigger stream.
```

### 3.3 Quarry (Каменоломня) — 2×2, 512×512

**`building_quarry_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval stone quarry, level 1.
A shallow dirt pit with raw grey stone boulders being mined,
a single wooden pickaxe stuck in a rock, a wooden bucket nearby,
some scattered stone chunks and gravel piles,
a simple wooden ladder leaning against the pit edge,
plain dirt-and-grass surroundings,
sitting on a 2x2 plot.
```

**`building_quarry_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medium medieval quarry, level 2 — upgraded from a small stone pit.
A stepped open pit with two terrace levels of grey stone,
a wooden crane with rope and pulley lifting a stone block,
a small wooden cart on rail tracks loaded with stones,
neatly piled cut stone blocks beside the pit,
miner's tools (pickaxes, hammers) leaning against a wooden tool shed,
small flagpole with mining banner,
sitting on a 2x2 plot with cobblestone path.
```

**`building_quarry_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A large industrial stone quarry, level 3 — heavily upgraded operation.
A deep multi-tier open pit with multiple terraces of grey and white marble,
two large wooden cranes lifting massive stone blocks,
multiple ore carts on a rail network with loaded stones,
big stacks of perfectly cut marble and granite blocks,
a workers' wooden building with chimney on one side,
torches lighting the darker corners, banners with mining guild emblem,
sitting on a 2x2 plot with paved roads.
```

### 3.4 Farm (Ферма) — 2×2, 512×512

**`building_farm_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval wheat farm field, level 1.
A 2x2 square plot of golden ripe wheat in tidy rows,
a wooden scarecrow with straw hat and tattered shirt in the center,
a small wooden hoe and bucket leaning at the corner,
low wooden fence around the perimeter,
a single small bush on one corner,
warm golden wheat color, ground showing rich brown soil.
```

**`building_farm_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medium medieval farm, level 2 — upgraded from a basic wheat field.
A neatly divided 2x2 plot with two crops: golden wheat and green vegetables in rows,
a small wooden windmill with rotating blades on one corner,
a hay cart loaded with bales next to a wooden barn doorway,
a wooden water trough, a chicken pecking at the ground,
proper wooden picket fence with gate,
warm sunlit farm vibe, mixed greens and golds.
```

**`building_farm_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A large prosperous farm estate, level 3 — heavily upgraded multi-crop operation.
Multiple sections of crops: wheat, corn, vegetables, herbs in colorful rows,
a tall red wooden barn with hay loft and weathervane,
a fully working wooden windmill with brown roof,
several haystacks, a horse-drawn plow, multiple farm animals (chickens, sheep, a cow),
flower garden border around the entire 2x2 plot,
stone-paved walking paths between sections, abundant and lively.
```

### 3.5 Storage / Warehouse (Склад) — 2×2, 512×512

**`building_storage_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval storage shed, level 1.
A simple wooden shed with thatched straw roof,
plain wooden plank walls, a single wooden door with iron latch,
a few wooden barrels and crates stacked outside,
a sack of grain leaning against the wall,
no windows, modest and plain,
sitting on a small grassy 2x2 plot.
```

**`building_storage_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval warehouse, level 2 — upgraded from a wooden shed.
A larger wooden building with stone foundation, red tile roof,
double wooden doors with metal hinges and a guild banner above,
multiple stacked wooden crates and barrels with rope-tied tarps next to it,
a small wooden loading platform with a cart parked beside it,
two small windows, a side awning storing extra sacks,
sitting on a cobblestone 2x2 plot.
```

**`building_storage_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A large fortified granary warehouse, level 3 — heavily upgraded.
A stone-walled multi-story building with reinforced wooden roof,
heavy iron-banded oak double doors with a lantern above,
ornate guild banner with crossed swords emblem,
a wooden crane attached to the upper floor for loading,
multiple stacks of crates, sealed barrels, gold-trimmed treasure chests visible,
torches mounted on the walls, stone-paved loading area with a horse cart,
sitting on a 2x2 plot, looks secure and important.
```

### 3.6 House (Жилой дом) — 1×1, 256×256

**`building_house_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval peasant hut, level 1.
A tiny wooden hut with thatched straw roof,
single wooden door, one small shutter window,
clay-and-wood walls (wattle and daub style),
a small wooden bench outside, a clay pot near the door,
modest peasant home vibe, warm earthy colors,
sitting on a small grassy 1x1 plot.
```

**`building_house_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval cottage, level 2 — upgraded from a peasant hut.
A timber-framed two-story cottage with stone foundation,
red clay tile roof with a small chimney emitting wispy smoke,
wooden front door with arched top, two glass windows with flower boxes,
a small wooden fence around a tiny front garden,
warm cozy home vibe, classic medieval village house,
sitting on a grassy 1x1 plot.
```

**`building_house_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A grand medieval townhouse, level 3 — heavily upgraded prestigious dwelling.
A larger three-story timber-framed manor with carved stone foundation,
elaborate red tile roof with dormer windows, two stone chimneys,
ornate wooden door with golden knocker, large arched windows with leaded glass,
flowering window boxes overflowing with colorful blooms,
a small private garden with a stone bench and rose bushes,
a stone-paved entrance path with lanterns,
sitting on a 1x1 plot, looks wealthy and beautiful.
```

### 3.7 Well (Колодец) — 1×1, 256×256

**`building_well_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple medieval village well, level 1.
A circular stone-rimmed well with rough grey stones,
a wooden bucket hanging from a short rope tied to a wooden crossbar,
a small wooden hand crank on the side,
some grass around the base,
modest functional water source,
sitting in the center of a small 1x1 plot.
```

**`building_well_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval covered well, level 2 — upgraded from a basic stone well.
A stone-rimmed circular well with a small wooden pavilion roof on four wooden posts,
the roof is shingled in dark wood with a metal weather vane on top,
a wooden bucket suspended from a proper iron-handled crank with chain,
ornate carved wooden details on the support posts,
a stone path leading to the well,
sitting on a 1x1 cobblestone plot.
```

**`building_well_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

An ornate medieval fountain-well, level 3 — heavily upgraded prestigious water source.
A circular polished marble fountain-well with carved stone reliefs,
a central tall stone pedestal topped with a small angelic statue spouting water,
crystal clear water glistening in the basin,
golden trim around the rim, decorative blue tile mosaic at the base,
flowering plants and small bushes around the marble plaza,
sitting on a 1x1 marble-paved plot.
```

### 3.8 Builder Hut (Хижина строителя) — 1×1, 256×256

**`building_builderhut_lvl1.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small medieval builder's workshop hut, level 1.
A modest wooden workshop hut with thatched straw roof,
an open-front design showing wooden tools (hammer, saw, axe) on a workbench,
a stack of fresh-cut wooden planks and a few bricks beside it,
a wooden ladder leaning against the side,
a worker's apron hanging on a peg,
warm brown wood tones, busy workshop vibe,
sitting on a small grassy 1x1 plot.
```

**`building_builderhut_lvl2.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval craftsman's workshop, level 2 — upgraded from a basic hut.
A larger wooden workshop with stone foundation and red tile roof,
a small chimney emitting smoke from a forge inside,
visible anvil with hammer, organized rows of tools on wall pegs,
multiple stacks of materials (bricks, planks, stone blocks) outside,
a wooden cart partially loaded with construction supplies,
a worker's wooden sign with a crossed-hammer guild emblem above the door,
sitting on a 1x1 cobblestone plot.
```

**`building_builderhut_lvl3.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A master builder's lodge, level 3 — heavily upgraded prestigious craftsmen's workshop.
A two-story timber-and-stone workshop with elaborate red tile roof,
multiple workstations visible: a forge with glowing embers, a stone-cutting bench, a carpentry table,
ornate guild banner with golden hammer-and-compass emblem above the entrance,
neatly organized tools, blueprints rolled up on a shelf,
abundant materials around (marble blocks, gold ingots in a chest, fine timber),
a master craftsman's wooden cart with rich materials,
sitting on a 1x1 polished stone plot, looks prestigious.
```

### 3.9 Construction state (общий для всех)

**`building_construction.png`** (256×256, scaled per building size)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval construction site, building under construction.
Wooden scaffolding poles arranged in a square pattern,
covered in tan-colored canvas tarps and ropes,
visible wooden support beams, some tools (hammer, ladder) at the base,
a small wooden sign with a hammer icon stuck in front,
ground around it has stone blocks and lumber piles,
no actual building visible inside — just the scaffolding cocoon.
Warm wooden tones, lively work-in-progress vibe.
```

> **Совет**: сгенерируй один construction-PNG с прозрачным фоном, и Phaser будет его масштабировать под 1×1 / 2×2 / 3×3 строительства. Не нужно три разных.

---

## 4. Resources Icons (6 штук, 128×128, ровный круглый формат)

> Иконки ресурсов — **компактные**, читаемые с 32-64 px. Объект по центру, минимальный фон, максимальная контрастность.

**`res_wood.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a stack of cut wooden logs,
3-4 brown logs with visible cross-section growth rings,
warm brown tones with darker outline,
the logs are cleanly stacked at slight angle for depth,
NO background frame or circle — just the logs floating with transparent background,
icon-style, cute cartoony, bold outline,
single isolated icon centered, very readable at small sizes.
```

**`res_stone.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a pile of grey stone blocks,
3-4 cubic grey stones stacked together,
clean facets, slight color variation in shades of grey,
bold dark outline, soft drop shadow underneath,
NO background frame, transparent background,
icon-style cartoony, very readable at small sizes.
```

**`res_food.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a golden loaf of crusty bread,
warm golden-brown color with bread texture lines,
small steam wisps rising from the top showing it's fresh,
bold dark outline, soft drop shadow,
NO background frame, transparent background,
icon-style cartoony, appetizing, very readable at small sizes.
```

**`res_water.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a single droplet of clear blue water,
classic teardrop shape with bright cyan-blue color,
small white highlight showing it's wet and reflective,
bold dark blue outline, soft drop shadow,
NO background frame, transparent background,
icon-style cartoony, very readable at small sizes.
```

**`res_gold.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a stack of shiny gold coins,
3-4 round gold coins stacked at a slight angle,
each coin with a star or crown emblem stamped on it,
bright shiny gold color with strong highlights,
bold dark outline, soft drop shadow,
NO background frame, transparent background,
icon-style cartoony, looks valuable.
```

**`res_gems.png`**
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon of a single large purple-pink crystal gemstone,
classic faceted diamond/crystal shape,
brilliant pink-purple color with strong white highlights and sparkles,
bold dark outline, sparkle effect around it,
NO background frame, transparent background,
icon-style cartoony, looks rare and premium.
```

---

## 5. Tiles (тайлы местности, 128×128)

Тайлы — **бесшовно тайлящиеся**. Это критично! Если края не стыкуются — вся карта будет в швах.

**Совет**: проще всего попросить генератор сделать "seamless tileable", потом проверить вручную: положить 4 тайла в сетку 2×2 в любом редакторе и увидеть швы или нет. Если есть — править вручную или перегенерить.

**`tile_grass_1.png` / `tile_grass_2.png` / `tile_grass_3.png`** (3 варианта для разнообразия)
```
A 2D mobile game seamless tile texture in the style of Clash of Clans and Hay Day,
top-down 30 degree view (matching the game perspective),
vibrant cartoon art style, saturated warm colors, soft cel-shading,
flat ground texture with no objects dominating, no shadows from objects,
edges must seamlessly tile with itself when placed in a grid,
opaque background (NOT transparent — this is ground),
square 1:1 aspect ratio, no UI elements, no labels, no text,
professional mobile game quality.

A seamless tileable game ground texture: green grass.
Top-down 30 degree view (matching the game perspective),
short tufted green grass with subtle color variation (lighter and darker patches),
some tiny pebbles and a few scattered yellow flowers (variant 2: small daisies, variant 3: a tiny mushroom),
bold cartoony style with subtle texture lines,
edges must seamlessly tile with itself when placed in a grid,
NO single object dominating, NO shadows from objects, just flat ground texture,
square 1:1 aspect ratio, transparent background NOT needed (ground tile is opaque).
```

**`tile_water.png`**
```
A 2D mobile game seamless tile texture in the style of Clash of Clans and Hay Day,
top-down 30 degree view (matching the game perspective),
vibrant cartoon art style, saturated warm colors, soft cel-shading,
flat ground texture with no objects dominating, no shadows from objects,
edges must seamlessly tile with itself when placed in a grid,
opaque background (NOT transparent — this is ground),
square 1:1 aspect ratio, no UI elements, no labels, no text,
professional mobile game quality.

A seamless tileable game ground texture: cartoon water.
Top-down 30 degree view, bright cyan-blue water with stylized waves and ripples,
small white wave crests and reflections,
edges seamlessly tileable in a grid,
flat tile texture, square 1:1 aspect ratio.
```

**`tile_road.png`**
```
A 2D mobile game seamless tile texture in the style of Clash of Clans and Hay Day,
top-down 30 degree view (matching the game perspective),
vibrant cartoon art style, saturated warm colors, soft cel-shading,
flat ground texture with no objects dominating, no shadows from objects,
edges must seamlessly tile with itself when placed in a grid,
opaque background (NOT transparent — this is ground),
square 1:1 aspect ratio, no UI elements, no labels, no text,
professional mobile game quality.

A seamless tileable game ground texture: cobblestone road.
Top-down 30 degree view, irregular grey cobblestones in a paved pattern,
some small grass tufts between stones, slight color variation,
edges seamlessly tileable in a grid,
flat texture, square 1:1 aspect ratio.
```

**`tile_dirt.png`**
```
A 2D mobile game seamless tile texture in the style of Clash of Clans and Hay Day,
top-down 30 degree view (matching the game perspective),
vibrant cartoon art style, saturated warm colors, soft cel-shading,
flat ground texture with no objects dominating, no shadows from objects,
edges must seamlessly tile with itself when placed in a grid,
opaque background (NOT transparent — this is ground),
square 1:1 aspect ratio, no UI elements, no labels, no text,
professional mobile game quality.

A seamless tileable game ground texture: brown dirt path.
Top-down 30 degree view, warm brown soil with subtle texture,
a few small pebbles, very subtle wear lines suggesting foot traffic,
edges seamlessly tileable in a grid,
flat texture, square 1:1 aspect ratio.
```

---

## 6. Decorations (декорации, 128-256 px)

**`deco_tree_oak.png`** (256×256)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval cartoon oak tree decoration, top-down 30 degree view,
lush rounded green canopy with lighter and darker patches showing leaf clusters,
sturdy brown trunk visible at the bottom,
soft circular ground shadow,
single isolated tree centered, transparent background.
```

**`deco_tree_pine.png`** (256×256)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval cartoon pine tree decoration, top-down 30 degree view,
tall conical shape with layered dark green needles,
narrow brown trunk visible at the base,
soft elliptical shadow underneath,
single isolated tree centered, transparent background.
```

**`deco_tree_dead.png`** (256×256)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A leafless gnarled cartoon tree decoration, top-down 30 degree view,
twisted bare brown branches reaching upward, no leaves,
slightly spooky but still stylized cartoon, not horror,
soft shadow underneath, single isolated tree centered, transparent background.
```

**`deco_rock_1.png` / `deco_rock_2.png`** (128×128)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small grey moss-covered rock cluster decoration,
top-down 30 degree view, irregular faceted stone shape with patches of green moss,
soft shadow underneath, single isolated cluster centered, transparent background.
```

**`deco_bush_1.png` / `deco_bush_2.png`** (128×128)
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A small green flowering bush decoration,
top-down 30 degree view, rounded leafy green bush with small colorful flowers (red, blue, yellow),
soft shadow underneath, single isolated bush centered, transparent background.
```

---

## 7. UI Elements

UI элементы — **другой стиль**: плоские, с минимальной перспективой, чистые контуры. Это интерфейс игры, не игровые объекты.

**`ui_button_build.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A wooden game UI button, rectangular with rounded corners,
top-down flat view (NOT 30-degree),
warm brown wood plank texture, golden metal corners and rivets,
a centered icon of a hammer-and-trowel in white silhouette,
no text on the button itself,
soft glow effect around the edges,
isolated transparent background.
```












**`ui_button_quests.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A wooden game UI button, rectangular with rounded corners,
warm brown wood plank texture, golden metal corners and rivets,
a centered icon of a rolled parchment scroll with a red wax seal in white-and-tan silhouette,
no text on the button itself,
soft golden glow effect around the edges,
isolated transparent background.
```

**`ui_button_friends.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A wooden game UI button, rectangular with rounded corners,
warm brown wood plank texture, golden metal corners and rivets,
a centered icon of two overlapping head-and-shoulders silhouettes (friends symbol) in white,
no text on the button itself,
soft golden glow effect around the edges,
isolated transparent background.
```

**`ui_button_shop.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A wooden game UI button, rectangular with rounded corners,
warm brown wood plank texture, golden metal corners and rivets,
a centered icon of a small cluster of three purple-pink crystal gems with sparkles,
no text on the button itself,
soft golden glow effect around the edges,
isolated transparent background.
```

**`ui_button_tech.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A wooden game UI button, rectangular with rounded corners,
warm brown wood plank texture, golden metal corners and rivets,
a centered icon of an open book with a glowing magic rune on the page in white-and-blue silhouette,
no text on the button itself,
soft golden glow effect around the edges,
isolated transparent background.
```

**`ui_button_close.png`** (128×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A small round close button for game UI,
deep red circular wooden frame with golden metal rim,
a thick white X cross symbol in the center,
soft drop shadow,
isolated transparent background.
```

**`ui_button_collect.png`** (256×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A glossy green action button for game UI, rectangular with rounded corners,
bright green gradient surface (lime to grass green),
golden metal corners and rivets,
a centered icon of a small treasure chest with a golden coin floating above it in silhouette,
soft inner highlight on the top half (glossy effect),
no text on the button itself,
soft golden glow effect around the edges,
isolated transparent background.
```

**`ui_panel_top_resources.png`** (1024×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A long horizontal wooden plank background panel for a game HUD,
warm brown wood texture with subtle grain lines,
golden metal corner brackets at all four corners with small rivets,
slightly arched top edge for elegance,
empty interior (no icons or text — just the empty plank),
soft drop shadow underneath,
isolated transparent background, aspect ratio approximately 8:1.
```

**`ui_panel_modal.png`** (768×1024)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A large rectangular wooden modal window background for a game UI,
warm brown wood plank texture (vertical planks),
ornate golden metal frame around the perimeter with small rivets and corner decorations,
a slightly darker inner area suggesting where content goes,
a small banner-style ribbon at the top (empty, for title),
soft inner shadow giving depth,
soft outer drop shadow,
isolated transparent background, vertical orientation (portrait).
```

**`ui_resource_chip.png`** (256×96)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A pill-shaped (oval) horizontal chip for displaying a single resource counter in a game HUD,
dark brown wooden background with slight gradient (lighter top, darker bottom),
golden metal thin rim around the perimeter,
empty interior (no icon, no number — placeholder for content overlay),
soft inner highlight at the top edge,
soft drop shadow underneath,
isolated transparent background, rounded pill shape.
```

**`ui_progress_bar_frame.png`** (512×64)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A horizontal progress bar frame for a game UI,
dark brown wooden frame with golden metal trim and small rivets at the corners,
empty hollow interior (the fill will be added in code on top),
slight inner shadow giving depth to the empty channel,
rounded ends, soft drop shadow underneath,
isolated transparent background.
```

**`ui_notification_toast.png`** (512×128)
```
A 2D mobile game UI asset in the style of Clash of Clans and Hay Day,
flat top-down view (no isometric angle),
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft drop shadow underneath,
fully transparent background (PNG with alpha channel),
single object centered in frame, no labels, no text,
clean isolated UI asset, professional mobile game quality.

A rounded rectangular notification banner for a game UI,
warm cream parchment texture with brown leather edges,
small golden metal corner studs,
empty interior (no icon, no text — placeholder for content),
soft drop shadow underneath,
isolated transparent background, horizontal orientation.
```


















---

## 8. Recipe для расширения промптов на весь GDD

Когда генеришь ассеты для остального контента из GDD — следуй формуле:

```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A {era_descriptor} {object_class}, level {N}.
{visual description: materials, shape, decorations}.
{environmental hints: ground, surroundings}.
{2-4 specific evolved details if level > 1}.
{tile_size} building, sitting on a {ground_type} plot.
```

Где:
- `era_descriptor` — `medieval` / `industrial` / `renaissance` / `magical` / `futuristic` / `cyberpunk`
- `object_class` — что строим (`smithy`, `temple`, `barracks` и т.д.)
- `level` — 1-50, но визуально различимы примерно 3-5 ступеней (lvl 1, 5, 15, 30, 50)

### Примеры расширения

**Кузница (Smithy) lvl 1**:
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval blacksmith's smithy, level 1.
A wooden open-air forge with stone furnace,
a black anvil on a stump, hammer and tongs leaning against it,
glowing red embers in the forge, smoke wisp rising,
metal scraps and a horseshoe on the ground,
2x2 building, sitting on a packed dirt plot.
```

**Магический алтарь (Magic Altar) lvl 1** (Эпоха 4):
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A magical mystic altar, level 1.
A short stone platform inscribed with glowing blue runes,
a small floating crystal sphere above the center emanating soft blue light,
crystal shards growing from the corners of the platform,
mystical purple smoke wisps swirling around it,
2x2 building, sitting on a moss-covered stone plot with glowing rune circle.
```

**Юнит копейщик (Spearman)**:
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A medieval spearman game character, top-down 3/4 view,
wearing brown leather armor and a metal helmet, holding a wooden spear with iron tip,
small round shield in the off-hand,
mid-stride walking pose, single isolated character on transparent background.
For animation: this is the IDLE pose, generate also walk_1, walk_2, walk_3, walk_4 frames.
```

**Иконка квеста (Quest icon)**:
```
A 2D mobile game asset in the style of Clash of Clans and Hay Day,
top-down 3/4 view at a 30 degree angle from above,
vibrant cartoon art style with bold black outlines (3 to 5 pixels thick),
saturated warm colors, soft cel-shading,
soft ground shadow baked under the object,
fully transparent background (PNG with alpha channel),
single object centered in frame, no UI elements, no labels, no text,
clean isolated game asset, professional mobile game quality.

A simple game icon: rolled parchment scroll with a wax seal,
the parchment has a tiny illustration of a lumbermill drawn on it,
warm cream paper color, red wax seal with star emblem,
NO background frame, transparent background,
icon-style cartoony, very readable at small sizes (64 px).
```

### Что менять при подъёме уровня здания

Чем выше уровень, тем должно быть:
1. **Больше деталей** (статуи, узоры, флаги)
2. **Богаче материалы** (дерево → камень → мрамор → золото)
3. **Больше окружающих элементов** (садик, фонтан, статуи рядом)
4. **Глубже цвета** (приглушённые → насыщенные → драгоценные)
5. **Больше движения / эффектов** (дым из трубы, вода в колесе, искры)
6. **Грандиознее**: lvl 1 = peasant, lvl 50 = royal palace

---

## 9. Чеклист проверки PNG перед коммитом

- [ ] Имя файла соответствует convention из секции 1.7
- [ ] Расширение `.png`, формат **PNG-32 RGBA**
- [ ] Размер по спеке (для здания — 256/512/768/1024 px)
- [ ] Фон **полностью прозрачный** (открой в Photopea — должна быть шахматка)
- [ ] Объект **по центру**, нижний край касается низа кадра (для зданий)
- [ ] Тень есть, но не превышает размер кадра
- [ ] Стиль соответствует Style Anchor (мульт, толстый outline, тёплые цвета)
- [ ] Нет UI-элементов / текста / водяных знаков на изображении
- [ ] Файл прогнан через `optipng -o7` (или аналог)
- [ ] Положен в правильную папку: `public/assets/{type}/`

## 10. Инструменты

| Задача | Рекомендация |
|---|---|
| Генерация | Midjourney v6+ / DALL·E 3 / ChatGPT image / Imagen 3 / SDXL+ControlNet |
| Удаление фона | remove.bg / built-in MJ alpha / Photopea |
| Просмотр прозрачности | Photopea (бесплатно, в браузере) |
| Кроп под bbox | ImageMagick: `magick file.png -trim +repage out.png` |
| Сжатие без потерь | optipng / pngcrush / pngquant |
| Создание спрайт-атласов | TexturePacker (платный) или free-tex-packer (open-source) |
| Бесшовные тайлы | проверка в Tiled или GIMP "Filter → Map → Make Seamless" |
| Превью грид-стенд | Photopea, размещаем в сетку 4×4 одинаковых тайлов |

---

## 11. Очередность генерации для MVP

Чтобы я мог максимально параллельно с тобой строить код — генерируй в этом порядке:

### День 1 — минимум для запуска карты
1. ✅ 6 иконок ресурсов (`res_*.png`)
2. ✅ 6 тайлов (`tile_grass_1/2/3, tile_water, tile_road, tile_dirt`)
3. ✅ Town Hall lvl 1 (одно здание для теста)
4. ✅ 1 декорация — `deco_tree_oak.png`

С этими **14 PNG** уже можно увидеть деревню в Phaser.

### День 2-3 — играбельный MVP
5. 7 оставшихся зданий × lvl 1 (Lumbermill, Quarry, Farm, Storage, House, Well, Builder Hut)
6. `building_construction.png`
7. Остальные декорации (pine, dead tree, 2 bush, 2 rock)

### День 4-5 — апгрейды и UI
8. Все здания lvl 2 (8 PNG)
9. UI кнопки (5 шт.) + 2 панели

### День 6+ — полировка
10. Все здания lvl 3 (8 PNG) — финальный визуал

Если генерация занимает больше — не страшно, я буду работать с placeholder-цветными квадратами, а ты подменяешь PNG по мере готовности. **Главное чтобы naming соблюдался** — тогда замена 1:1.

---

## 12. Важно: про итерации

Первая генерация **никогда** не идеальна. Будет 5-10 итераций каждого здания. Закладывай это во время.

Используй **тот же seed** в Midjourney (`--seed N`), если нашёл удачный — это поможет сохранять стиль через все уровни.

В DALL·E / ChatGPT — копируй удачный промпт и меняй **только** уровень, чтобы стиль держался.

Когда что-то получилось крутое — **сохрани промпт в этот md-файл** в раздел `## 13. Промпты, которые сработали хорошо`. Тогда можно повторить.

## 13. Промпты, которые сработали хорошо

(Заполняется по мере итераций.)

```
TBD — после первых успешных генераций.
```
