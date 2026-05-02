import Phaser from "phaser";
import { api, haptic, hapticNotify } from "../api";
import type { BuildingDef, BuildingSnap, ConfigSnap, StateSnap } from "../types";
import { showBuildMenu, showBuildingDetails, toast } from "../ui/hud";

const TILE = 96;          // visual tile px (texture is 128, we render at 96 for density)
const MAP_PADDING = 64;

export class MapScene extends Phaser.Scene {
  private mapW = 16;
  private mapH = 16;
  private worldLayer!: Phaser.GameObjects.Container;
  private gridLayer!: Phaser.GameObjects.Graphics;
  private buildingsLayer!: Phaser.GameObjects.Container;
  private decoLayer!: Phaser.GameObjects.Container;

  private placementGhost: Phaser.GameObjects.Container | null = null;
  private placementType: string | null = null;

  private buildingSprites: Map<number, Phaser.GameObjects.Container> = new Map();

  private cam!: Phaser.Cameras.Scene2D.Camera;
  private dragStart: { x: number; y: number } | null = null;
  private dragMoved = false;
  private pinchInitial: number | null = null;

  constructor() { super("MapScene"); }

  create() {
    const config = this.registry.get("config") as ConfigSnap;
    const state = this.registry.get("state") as StateSnap;
    [this.mapW, this.mapH] = state.map_size;

    this.cam = this.cameras.main;
    this.cam.setBackgroundColor("#5BAE5B");

    // World container holds everything that pans/zooms together.
    this.worldLayer = this.add.container(MAP_PADDING, MAP_PADDING);

    this.drawGround();
    this.gridLayer = this.add.graphics();
    this.worldLayer.add(this.gridLayer);
    this.gridLayer.setVisible(false);

    this.decoLayer = this.add.container(0, 0);
    this.worldLayer.add(this.decoLayer);
    this.spawnDecorations();

    this.buildingsLayer = this.add.container(0, 0);
    this.worldLayer.add(this.buildingsLayer);

    for (const b of state.buildings) {
      this.spawnBuildingSprite(b, config);
    }

    // Center camera on Town Hall by default.
    const hall = state.buildings.find((b) => b.type === "townhall");
    if (hall) {
      const def = config.buildings["townhall"];
      const [w, h] = def.size;
      const cx = MAP_PADDING + (hall.x + w / 2) * TILE;
      const cy = MAP_PADDING + (hall.y + h / 2) * TILE;
      this.cam.centerOn(cx, cy);
    }

    this.setupInput();
    this.setupEvents();

    // Periodic refresh ~ every 30s to pick up finished jobs.
    this.time.addEvent({
      delay: 30_000,
      loop: true,
      callback: () => this.refreshState(),
    });

    this.scale.on("resize", () => {
      // Phaser resizes auto; nothing extra.
    });
  }

  private drawGround() {
    const w = this.mapW * TILE;
    const h = this.mapH * TILE;
    // Big rounded rect representing the village land.
    const g = this.add.graphics();
    g.fillStyle(0x6FBE5B, 1);
    g.fillRoundedRect(0, 0, w, h, 24);
    g.lineStyle(6, 0x3F7D3D, 1);
    g.strokeRoundedRect(0, 0, w, h, 24);
    // Subtle inner texture rows.
    g.fillStyle(0x67B055, 0.5);
    for (let y = 0; y < this.mapH; y++) {
      for (let x = 0; x < this.mapW; x++) {
        if ((x + y) % 2 === 0) {
          g.fillRect(x * TILE, y * TILE, TILE, TILE);
        }
      }
    }
    this.worldLayer.add(g);
  }

  private spawnDecorations() {
    // Sprinkle decorative trees/rocks/bushes around the empty edges.
    const decoTypes = ["deco_tree_oak", "deco_tree_pine", "deco_bush_1", "deco_bush_2", "deco_rock_1", "deco_rock_2"];
    const positions: Array<[number, number]> = [];
    const rng = Phaser.Math.RandomDataGenerator.prototype;
    let seed = 1337;
    const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };

    for (let i = 0; i < 20; i++) {
      const x = Math.floor(rand() * this.mapW);
      const y = Math.floor(rand() * this.mapH);
      // Keep center clear (Town Hall lands at 6,6 with size 3).
      if (x >= 5 && x <= 9 && y >= 5 && y <= 9) continue;
      const key = decoTypes[Math.floor(rand() * decoTypes.length)];
      if (!this.textures.exists(key)) continue;
      const img = this.add.image(x * TILE + TILE / 2, y * TILE + TILE * 0.85, key);
      img.setOrigin(0.5, 1);
      const scale = key.includes("tree") ? 0.55 : 0.4;
      img.setScale(scale);
      img.setDepth(y);
      this.decoLayer.add(img);
      positions.push([x, y]);
    }
  }

  private setupInput() {
    let pointer1: { x: number; y: number } | null = null;
    let pointer2: { x: number; y: number } | null = null;

    this.input.on("pointerdown", (p: Phaser.Input.Pointer) => {
      if (this.placementGhost) {
        // Tap to place if we have an active placement.
        this.tryPlaceFromGhost();
        return;
      }
      this.dragStart = { x: p.x, y: p.y };
      this.dragMoved = false;
    });

    this.input.on("pointermove", (p: Phaser.Input.Pointer) => {
      if (this.placementGhost) {
        const wp = this.cameraToWorld(p.x, p.y);
        const tile = this.worldToTile(wp.x, wp.y);
        this.moveGhost(tile.x, tile.y);
        return;
      }

      if (!p.isDown || !this.dragStart) return;

      // Pinch zoom (multi-pointer).
      const pointers = this.input.manager.pointers.filter((pt: any) => pt.isDown && pt.id > 0);
      if (pointers.length >= 2) {
        const [pa, pb] = pointers;
        const d = Phaser.Math.Distance.Between(pa.x, pa.y, pb.x, pb.y);
        if (this.pinchInitial == null) {
          this.pinchInitial = d / this.cam.zoom;
        } else {
          this.cam.setZoom(Phaser.Math.Clamp(d / this.pinchInitial, 0.4, 1.6));
        }
        return;
      }

      const dx = p.x - p.prevPosition.x;
      const dy = p.y - p.prevPosition.y;
      if (Math.abs(p.x - this.dragStart.x) + Math.abs(p.y - this.dragStart.y) > 6) {
        this.dragMoved = true;
      }
      this.cam.scrollX -= dx / this.cam.zoom;
      this.cam.scrollY -= dy / this.cam.zoom;
      this.clampCamera();
    });

    this.input.on("pointerup", (p: Phaser.Input.Pointer) => {
      this.pinchInitial = null;
      const wasDrag = this.dragMoved;
      this.dragStart = null;
      this.dragMoved = false;
      if (this.placementGhost) return;
      if (wasDrag) return;

      // Tap detection — find building at this point.
      const wp = this.cameraToWorld(p.x, p.y);
      const tile = this.worldToTile(wp.x, wp.y);
      const hit = this.findBuildingAt(tile.x, tile.y);
      if (hit) {
        haptic("light");
        showBuildingDetails(this, hit);
      }
    });

    // Wheel zoom (desktop).
    this.input.on("wheel", (_p: Phaser.Input.Pointer, _objs: any, _dx: number, dy: number) => {
      const z = Phaser.Math.Clamp(this.cam.zoom * (dy > 0 ? 0.9 : 1.1), 0.4, 1.6);
      this.cam.setZoom(z);
    });
  }

  private setupEvents() {
    // From HUD: open build menu.
    this.events.on("ui:open_build_menu", () => {
      const config = this.registry.get("config") as ConfigSnap;
      const state = this.registry.get("state") as StateSnap;
      showBuildMenu(this, config, state);
    });

    this.events.on("ui:place", (type: string) => {
      this.beginPlacement(type);
    });

    this.events.on("ui:cancel_placement", () => {
      this.cancelPlacement();
    });

    this.events.on("ui:state_updated", (state: StateSnap) => {
      this.applyState(state);
    });
  }

  private cameraToWorld(sx: number, sy: number) {
    const wp = this.cam.getWorldPoint(sx, sy);
    return { x: wp.x - MAP_PADDING, y: wp.y - MAP_PADDING };
  }

  private worldToTile(wx: number, wy: number) {
    return {
      x: Phaser.Math.Clamp(Math.floor(wx / TILE), 0, this.mapW - 1),
      y: Phaser.Math.Clamp(Math.floor(wy / TILE), 0, this.mapH - 1),
    };
  }

  private findBuildingAt(tileX: number, tileY: number): BuildingSnap | null {
    const config = this.registry.get("config") as ConfigSnap;
    const state = this.registry.get("state") as StateSnap;
    for (const b of state.buildings) {
      const def = config.buildings[b.type];
      if (!def) continue;
      const [w, h] = def.size;
      if (tileX >= b.x && tileX < b.x + w && tileY >= b.y && tileY < b.y + h) {
        return b;
      }
    }
    return null;
  }

  private clampCamera() {
    const w = this.mapW * TILE + MAP_PADDING * 2;
    const h = this.mapH * TILE + MAP_PADDING * 2;
    this.cam.setBounds(-MAP_PADDING * 2, -MAP_PADDING * 2, w + MAP_PADDING * 4, h + MAP_PADDING * 4);
  }

  // ---------- placement ----------

  beginPlacement(type: string) {
    this.cancelPlacement();
    const config = this.registry.get("config") as ConfigSnap;
    const def = config.buildings[type];
    if (!def) return;
    this.placementType = type;
    this.gridLayer.setVisible(true);
    this.drawGrid();

    const ghost = this.add.container(0, 0);
    const [w, h] = def.size;
    const bg = this.add.rectangle(0, 0, w * TILE, h * TILE, 0x6FE085, 0.35);
    bg.setOrigin(0, 0);
    bg.setStrokeStyle(3, 0x6FE085, 1);
    ghost.add(bg);

    const tex = `b_${type}_1`;
    if (this.textures.exists(tex)) {
      const sprite = this.add.image(w * TILE / 2, h * TILE * 0.95, tex);
      sprite.setOrigin(0.5, 1);
      const targetW = w * TILE * 0.95;
      sprite.setDisplaySize(targetW, sprite.height * (targetW / sprite.width));
      sprite.setAlpha(0.85);
      ghost.add(sprite);
    }

    this.worldLayer.add(ghost);
    this.placementGhost = ghost;
    this.moveGhost(Math.floor(this.mapW / 2) - Math.floor(w / 2), Math.floor(this.mapH / 2) - Math.floor(h / 2));
  }

  private moveGhost(tx: number, ty: number) {
    if (!this.placementGhost || !this.placementType) return;
    const config = this.registry.get("config") as ConfigSnap;
    const def = config.buildings[this.placementType];
    const [w, h] = def.size;
    tx = Phaser.Math.Clamp(tx, 0, this.mapW - w);
    ty = Phaser.Math.Clamp(ty, 0, this.mapH - h);
    this.placementGhost.setPosition(tx * TILE, ty * TILE);
    this.placementGhost.setData("tile", { x: tx, y: ty });

    // Color the ghost rect by validity.
    const valid = this.isPositionFree(tx, ty, w, h);
    const rect = this.placementGhost.list[0] as Phaser.GameObjects.Rectangle;
    rect.setFillStyle(valid ? 0x6FE085 : 0xE74C3C, 0.35);
    rect.setStrokeStyle(3, valid ? 0x6FE085 : 0xE74C3C, 1);
  }

  private isPositionFree(tx: number, ty: number, w: number, h: number): boolean {
    const config = this.registry.get("config") as ConfigSnap;
    const state = this.registry.get("state") as StateSnap;
    for (const b of state.buildings) {
      const def = config.buildings[b.type];
      if (!def) continue;
      const [bw, bh] = def.size;
      if (tx + w > b.x && b.x + bw > tx && ty + h > b.y && b.y + bh > ty) {
        return false;
      }
    }
    return true;
  }

  private async tryPlaceFromGhost() {
    if (!this.placementGhost || !this.placementType) return;
    const tile = this.placementGhost.getData("tile") as { x: number; y: number };
    const type = this.placementType;
    const cancel = () => this.cancelPlacement();

    haptic("medium");
    const r = await api.build(type, tile.x, tile.y);
    if (!r.ok) {
      hapticNotify("error");
      toast(this.translateError(r.error, r.missing), "error");
      return;
    }
    cancel();
    if (r.data?.state) this.applyState(r.data.state);
    hapticNotify("success");
    toast("Стройка пошла!", "success");
  }

  private translateError(err?: string, missing?: string): string {
    switch (err) {
      case "not_enough_resources": return missing ? `Не хватает ресурса: ${missing}` : "Не хватает ресурсов";
      case "position_occupied": return "Место занято";
      case "invalid_position": return "Нельзя строить здесь";
      case "limit_reached": return "Лимит этого здания достигнут";
      case "no_builder_slots": return "Все строители заняты";
      case "era_locked": return "Открывается в следующей эпохе";
      case "max_level": return "Уже максимальный уровень";
      case "busy": return "Здание сейчас занято";
      default: return err || "Ошибка";
    }
  }

  cancelPlacement() {
    if (this.placementGhost) {
      this.placementGhost.destroy();
      this.placementGhost = null;
    }
    this.placementType = null;
    this.gridLayer.setVisible(false);
    this.gridLayer.clear();
  }

  private drawGrid() {
    this.gridLayer.clear();
    this.gridLayer.lineStyle(1, 0xFFFFFF, 0.25);
    for (let x = 0; x <= this.mapW; x++) {
      this.gridLayer.lineBetween(x * TILE, 0, x * TILE, this.mapH * TILE);
    }
    for (let y = 0; y <= this.mapH; y++) {
      this.gridLayer.lineBetween(0, y * TILE, this.mapW * TILE, y * TILE);
    }
  }

  // ---------- buildings ----------

  private spawnBuildingSprite(b: BuildingSnap, config: ConfigSnap) {
    const def = config.buildings[b.type];
    if (!def) return;
    const [w, h] = def.size;

    const container = this.add.container(b.x * TILE, b.y * TILE);

    let textureKey: string;
    if (b.status === "building") {
      textureKey = "b_construction";
    } else {
      textureKey = `b_${b.type}_${b.level}`;
      if (!this.textures.exists(textureKey)) textureKey = `b_${b.type}_1`;
    }

    if (this.textures.exists(textureKey)) {
      const sprite = this.add.image(w * TILE / 2, h * TILE * 0.95, textureKey);
      sprite.setOrigin(0.5, 1);
      const targetW = w * TILE * 0.95;
      sprite.setDisplaySize(targetW, sprite.height * (targetW / sprite.width));
      container.add(sprite);
      container.setData("sprite", sprite);
    }

    container.setSize(w * TILE, h * TILE);
    container.setDepth(b.y + h);  // for proper z-ordering across building rows
    this.buildingsLayer.add(container);
    this.buildingSprites.set(b.id, container);

    // Subtle pop-in.
    container.setScale(0);
    this.tweens.add({
      targets: container,
      scale: 1,
      duration: 300,
      ease: "Back.out",
    });
  }

  private removeBuildingSprite(id: number) {
    const c = this.buildingSprites.get(id);
    if (c) {
      this.tweens.add({
        targets: c,
        scale: 0,
        duration: 200,
        ease: "Back.in",
        onComplete: () => c.destroy(),
      });
      this.buildingSprites.delete(id);
    }
  }

  applyState(state: StateSnap) {
    this.registry.set("state", state);
    const config = this.registry.get("config") as ConfigSnap;

    // Sync sprites to state (add new, remove deleted, update changed).
    const seen = new Set<number>();
    for (const b of state.buildings) {
      seen.add(b.id);
      const existing = this.buildingSprites.get(b.id);
      if (!existing) {
        this.spawnBuildingSprite(b, config);
        continue;
      }
      // Update sprite if state changed.
      const def = config.buildings[b.type];
      if (!def) continue;
      const [w, h] = def.size;
      existing.setPosition(b.x * TILE, b.y * TILE);
      existing.setDepth(b.y + h);
      let key: string;
      if (b.status === "building" || b.status === "upgrading") {
        key = b.status === "building" ? "b_construction" : `b_${b.type}_${b.level}`;
      } else {
        key = `b_${b.type}_${b.level}`;
      }
      if (!this.textures.exists(key)) key = `b_${b.type}_1`;
      const sprite = existing.getData("sprite") as Phaser.GameObjects.Image | undefined;
      if (sprite && sprite.texture.key !== key) {
        sprite.setTexture(key);
        const targetW = w * TILE * 0.95;
        sprite.setDisplaySize(targetW, sprite.height * (targetW / sprite.width));
      }
    }
    for (const id of Array.from(this.buildingSprites.keys())) {
      if (!seen.has(id)) this.removeBuildingSprite(id);
    }

    this.events.emit("ui:rerender_hud", state);
  }

  async refreshState() {
    const r = await api.state();
    if (r.ok && r.data) this.applyState(r.data);
  }
}
