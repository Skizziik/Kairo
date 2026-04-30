"""Market asset catalog — 294 assets across 8 categories.

Each asset spec drives initial seeding of market_assets table. After seeding
the DB row is the source of truth (current_price evolves; the catalog stays
static).

Format: (key, name, symbol, subcategory, rarity, base_price, volatility,
        liquidity, tags, cycle_period_h, cycle_amplitude)

Volatility: 0.05 (boring) - 1.0 (insane meme coin)
Liquidity: 0.1 (rare materials, hard to sell) - 1.0 (BTC, instant)
Cycle period in hours, amplitude 0..0.05 (5% sine wave overlay).
"""

# Category → list of asset specs
CATEGORIES = {
    "crypto":  "🪙 Криптовалюты",
    "metals":  "⚙ Металлы",
    "energy":  "⚡ Энергия",
    "stocks":  "🏢 Игровые акции",
    "tech":    "🌐 Tech-гиганты",
    "rare":    "💎 Редкие материалы",
    "agro":    "🌾 Агро/Сырьё",
    "indexes": "📈 Индексы",
}


# ============================================================
# CRYPTO (50)
# ============================================================
CRYPTO_ASSETS = [
    # Mainstream (10)
    ("btc", "Bitcoin", "BTC", "mainstream", "epic", 65000_00, 0.55, 1.0, ["crypto","store_of_value"], 12, 0.02),
    ("eth", "Ethereum", "ETH", "mainstream", "epic", 3500_00, 0.60, 1.0, ["crypto","smart_contract","defi"], 8, 0.025),
    ("sol", "Solana", "SOL", "mainstream", "rare", 180_00, 0.75, 0.95, ["crypto","smart_contract","fast"], 6, 0.03),
    ("ton", "Toncoin", "TON", "mainstream", "rare", 7_00, 0.70, 0.90, ["crypto","telegram","fast"], 5, 0.03),
    ("ada", "Cardano", "ADA", "mainstream", "uncommon", 50, 0.55, 0.90, ["crypto","smart_contract","pos"], 7, 0.025),
    ("dot", "Polkadot", "DOT", "mainstream", "uncommon", 8_00, 0.60, 0.85, ["crypto","interop"], 6, 0.025),
    ("avax", "Avalanche", "AVAX", "mainstream", "rare", 35_00, 0.70, 0.85, ["crypto","smart_contract"], 5, 0.03),
    ("link", "Chainlink", "LINK", "mainstream", "rare", 18_00, 0.55, 0.90, ["crypto","oracle","defi"], 8, 0.025),
    ("matic", "Polygon", "MATIC", "mainstream", "uncommon", 70, 0.60, 0.90, ["crypto","l2","scaling"], 6, 0.025),
    ("xrp", "Ripple", "XRP", "mainstream", "uncommon", 60, 0.50, 0.95, ["crypto","payments"], 9, 0.02),

    # Altcoins (10)
    ("ltc", "Litecoin", "LTC", "altcoin", "uncommon", 75_00, 0.45, 0.95, ["crypto","payments"], 10, 0.02),
    ("trx", "Tron", "TRX", "altcoin", "uncommon", 15, 0.55, 0.85, ["crypto","smart_contract"], 6, 0.025),
    ("atom", "Cosmos", "ATOM", "altcoin", "uncommon", 9_00, 0.55, 0.85, ["crypto","interop"], 7, 0.025),
    ("near", "NEAR Protocol", "NEAR", "altcoin", "uncommon", 5_00, 0.65, 0.80, ["crypto","smart_contract","ai"], 5, 0.03),
    ("algo", "Algorand", "ALGO", "altcoin", "common", 20, 0.65, 0.80, ["crypto","pos"], 6, 0.025),
    ("uni", "Uniswap", "UNI", "altcoin", "uncommon", 8_00, 0.60, 0.85, ["crypto","defi","dex"], 7, 0.03),
    ("icp", "Internet Computer", "ICP", "altcoin", "uncommon", 12_00, 0.70, 0.75, ["crypto","compute"], 5, 0.03),
    ("fil", "Filecoin", "FIL", "altcoin", "uncommon", 6_00, 0.60, 0.80, ["crypto","storage"], 8, 0.025),
    ("xmr", "Monero", "XMR", "altcoin", "rare", 160_00, 0.50, 0.70, ["crypto","privacy"], 9, 0.02),
    ("hbar", "Hedera", "HBAR", "altcoin", "common", 7, 0.65, 0.75, ["crypto","enterprise"], 6, 0.025),

    # Memecoins (10) — extreme volatility, tiny prices
    ("doge", "Dogecoin", "DOGE", "meme", "uncommon", 15, 0.85, 0.95, ["crypto","meme","dog"], 4, 0.04),
    ("shib", "Shiba Token", "SHIB", "meme", "common", 2, 0.95, 0.85, ["crypto","meme","dog"], 3, 0.05),
    ("pepe", "Pepe Coin", "PEPE", "meme", "common", 1, 1.00, 0.75, ["crypto","meme","frog"], 2, 0.05),
    ("meme_frog", "Meme Frog", "FROG", "meme", "common", 3, 1.00, 0.50, ["crypto","meme","frog"], 2, 0.05),
    ("wojak", "Wojak Coin", "WJK", "meme", "common", 1, 1.00, 0.40, ["crypto","meme"], 2, 0.05),
    ("chad", "Chad Coin", "CHAD", "meme", "common", 2, 1.00, 0.45, ["crypto","meme"], 2, 0.05),
    ("banana", "Banana Token", "NANA", "meme", "common", 1, 1.00, 0.35, ["crypto","meme"], 2, 0.05),
    ("cat", "Cat Coin", "CATS", "meme", "common", 1, 1.00, 0.40, ["crypto","meme","cat"], 2, 0.05),
    ("moon", "Moon Token", "MOON", "meme", "common", 1, 1.00, 0.30, ["crypto","meme"], 2, 0.05),
    ("rocket", "Rocket Token", "RKT", "meme", "common", 2, 1.00, 0.50, ["crypto","meme"], 2, 0.05),

    # In-game (5)
    ("tryll", "Tryll Coin", "TRYLL", "ingame", "rare", 240, 0.80, 0.85, ["crypto","tryll","gaming"], 4, 0.04),
    ("snake", "Snake Coin", "SNAKE", "ingame", "uncommon", 80, 0.85, 0.70, ["crypto","tryll","gaming","meme"], 3, 0.04),
    ("ghost", "Ghost Coin", "GHOST", "ingame", "rare", 12_00, 0.95, 0.55, ["crypto","tryll","gaming","meme"], 3, 0.05),
    ("pluma", "Pluma Token", "PLM", "ingame", "uncommon", 1_00, 0.65, 0.85, ["crypto","tryll","flappy"], 5, 0.03),
    ("chip", "Casino Chip", "CHIP", "ingame", "uncommon", 5_00, 0.55, 0.90, ["crypto","tryll","casino"], 6, 0.025),

    # Extra crypto (15)
    ("bnb", "Binance Coin", "BNB", "altcoin", "rare", 580_00, 0.55, 0.95, ["crypto","exchange","mainstream"], 8, 0.025),
    ("usdt", "Tether", "USDT", "altcoin", "common", 1_00, 0.05, 1.0, ["crypto","stablecoin"], 24, 0.001),
    ("usdc", "USD Coin", "USDC", "altcoin", "common", 1_00, 0.04, 1.0, ["crypto","stablecoin"], 24, 0.001),
    ("bch", "Bitcoin Cash", "BCH", "altcoin", "uncommon", 380_00, 0.55, 0.85, ["crypto","btc_fork"], 9, 0.025),
    ("arb", "Arbitrum", "ARB", "altcoin", "uncommon", 1_00, 0.65, 0.85, ["crypto","l2","scaling"], 5, 0.03),
    ("op", "Optimism", "OP", "altcoin", "uncommon", 2_50, 0.65, 0.85, ["crypto","l2","scaling"], 5, 0.03),
    ("apt", "Aptos", "APT", "altcoin", "uncommon", 9_00, 0.70, 0.80, ["crypto","smart_contract"], 5, 0.03),
    ("sui", "Sui", "SUI", "altcoin", "uncommon", 1_50, 0.75, 0.75, ["crypto","smart_contract"], 4, 0.035),
    ("stx", "Stacks", "STX", "altcoin", "uncommon", 2_00, 0.65, 0.70, ["crypto","btc_layer"], 6, 0.03),
    ("inj", "Injective", "INJ", "altcoin", "uncommon", 30_00, 0.75, 0.80, ["crypto","defi"], 5, 0.035),
    ("wif", "dogwifhat", "WIF", "meme", "common", 3_00, 1.00, 0.55, ["crypto","meme","dog"], 2, 0.06),
    ("bonk", "Bonk", "BONK", "meme", "common", 2, 1.00, 0.65, ["crypto","meme","dog"], 2, 0.06),
    ("floki", "Floki", "FLOKI", "meme", "common", 20, 1.00, 0.50, ["crypto","meme","dog"], 2, 0.05),
    ("pizza", "Pizza Token", "PIZZA", "meme", "common", 5, 1.00, 0.30, ["crypto","meme"], 2, 0.05),
    ("void_coin", "Void Coin", "VOIDC", "ingame", "epic", 50_00, 0.95, 0.40, ["crypto","tryll","void","meme"], 3, 0.06),
]


# ============================================================
# METALS (43)
# ============================================================
METAL_ASSETS = [
    # Precious (6)
    ("gold", "Gold", "XAU", "precious", "rare", 2400_00, 0.18, 0.95, ["metal","precious","safe_haven","jewelry"], 24, 0.008),
    ("silver", "Silver", "XAG", "precious", "uncommon", 30_00, 0.25, 0.90, ["metal","precious","industrial","jewelry"], 18, 0.012),
    ("platinum", "Platinum", "XPT", "precious", "rare", 950_00, 0.30, 0.80, ["metal","precious","auto","jewelry"], 18, 0.012),
    ("palladium", "Palladium", "XPD", "precious", "rare", 1000_00, 0.35, 0.75, ["metal","precious","auto"], 16, 0.015),
    ("rhodium", "Rhodium", "XRH", "precious", "epic", 5000_00, 0.45, 0.50, ["metal","precious","auto","industrial"], 12, 0.02),
    ("iridium", "Iridium", "XIR", "precious", "epic", 4500_00, 0.40, 0.50, ["metal","precious","industrial"], 12, 0.02),

    # Industrial (12)
    ("copper", "Copper", "CU", "industrial", "common", 9000_00, 0.20, 0.95, ["metal","industrial","construction","wiring"], 24, 0.008),
    ("iron", "Iron", "FE", "industrial", "common", 110_00, 0.18, 0.95, ["metal","industrial","construction","steel"], 24, 0.007),
    ("aluminum", "Aluminum", "AL", "industrial", "common", 2500_00, 0.22, 0.90, ["metal","industrial","auto","aerospace"], 20, 0.01),
    ("zinc", "Zinc", "ZN", "industrial", "common", 2800_00, 0.25, 0.85, ["metal","industrial","construction"], 20, 0.01),
    ("nickel", "Nickel", "NI", "industrial", "uncommon", 18000_00, 0.35, 0.75, ["metal","industrial","battery","steel"], 16, 0.015),
    ("lead", "Lead", "PB", "industrial", "common", 2100_00, 0.20, 0.85, ["metal","industrial","battery"], 22, 0.008),
    ("tin", "Tin", "SN", "industrial", "uncommon", 32000_00, 0.30, 0.70, ["metal","industrial","electronics"], 18, 0.012),
    ("titanium", "Titanium", "TI", "industrial", "rare", 9500_00, 0.30, 0.70, ["metal","industrial","aerospace","medical"], 16, 0.013),
    ("tungsten", "Tungsten", "W", "industrial", "rare", 35000_00, 0.30, 0.55, ["metal","industrial","tools"], 14, 0.014),
    ("magnesium", "Magnesium", "MG", "industrial", "uncommon", 4000_00, 0.25, 0.80, ["metal","industrial","auto"], 18, 0.011),
    ("chromium", "Chromium", "CR", "industrial", "uncommon", 12000_00, 0.30, 0.70, ["metal","industrial","stainless_steel"], 16, 0.012),
    ("manganese", "Manganese", "MN", "industrial", "uncommon", 2200_00, 0.30, 0.75, ["metal","industrial","battery","steel"], 16, 0.013),

    # Strategic (6)
    ("lithium", "Lithium", "LI", "strategic", "rare", 22000_00, 0.50, 0.65, ["metal","strategic","battery","ev"], 12, 0.025),
    ("cobalt", "Cobalt", "CO", "strategic", "rare", 33000_00, 0.50, 0.55, ["metal","strategic","battery","ev"], 12, 0.025),
    ("neodymium", "Neodymium", "ND", "strategic", "epic", 70000_00, 0.45, 0.45, ["metal","strategic","ev","wind"], 14, 0.022),
    ("uranium", "Uranium", "U", "strategic", "epic", 90_00, 0.55, 0.50, ["metal","strategic","nuclear","energy"], 12, 0.028),
    ("rare_earths", "Rare Earths", "REE", "strategic", "epic", 12000_00, 0.55, 0.45, ["metal","strategic","tech","ev"], 11, 0.03),
    ("tantalum", "Tantalum", "TA", "strategic", "rare", 280_00, 0.40, 0.50, ["metal","strategic","electronics"], 14, 0.02),

    # Fantasy (4)
    ("mythril", "Mythril", "MTH", "fantasy", "legendary", 500000_00, 0.85, 0.20, ["metal","fantasy","mythic"], 8, 0.05),
    ("adamantite", "Adamantite", "ADM", "fantasy", "legendary", 800000_00, 0.85, 0.20, ["metal","fantasy","mythic"], 8, 0.05),
    ("orichalcum", "Orichalcum", "ORC", "fantasy", "mythic", 2500000_00, 0.95, 0.15, ["metal","fantasy","mythic"], 6, 0.06),
    ("star_metal_m", "Star Metal", "STAR", "fantasy", "mythic", 5000000_00, 0.95, 0.10, ["metal","fantasy","cosmic"], 6, 0.06),

    # Extra metals (15)
    ("bismuth", "Bismuth", "BI", "industrial", "uncommon", 6000_00, 0.30, 0.65, ["metal","industrial","cosmetic"], 18, 0.012),
    ("mercury", "Mercury", "HG", "industrial", "rare", 800_00, 0.35, 0.40, ["metal","industrial","chemical","toxic"], 16, 0.015),
    ("brass", "Brass", "BR", "industrial", "common", 7000_00, 0.20, 0.85, ["metal","industrial","alloy","instrument"], 22, 0.008),
    ("bronze", "Bronze", "BRZ", "industrial", "common", 6500_00, 0.20, 0.85, ["metal","industrial","alloy","art"], 22, 0.008),
    ("steel", "Steel", "STL", "industrial", "common", 800_00, 0.18, 0.95, ["metal","industrial","construction","auto"], 24, 0.007),
    ("osmium", "Osmium", "OS", "industrial", "epic", 400_00, 0.40, 0.30, ["metal","industrial","densest"], 14, 0.018),
    ("gallium", "Gallium", "GA", "industrial", "rare", 580_00, 0.40, 0.50, ["metal","industrial","semiconductor"], 14, 0.02),
    ("indium", "Indium", "IN", "industrial", "rare", 320_00, 0.40, 0.50, ["metal","industrial","display"], 14, 0.02),
    ("scandium", "Scandium", "SC", "strategic", "rare", 30000_00, 0.45, 0.40, ["metal","strategic","aerospace","sports"], 14, 0.022),
    ("vanadium", "Vanadium", "V", "industrial", "uncommon", 3500_00, 0.30, 0.65, ["metal","industrial","steel","battery"], 16, 0.014),
    ("molybdenum", "Molybdenum", "MO", "industrial", "uncommon", 4200_00, 0.30, 0.70, ["metal","industrial","steel"], 16, 0.013),
    ("zirconium", "Zirconium", "ZR", "industrial", "uncommon", 2800_00, 0.25, 0.75, ["metal","industrial","nuclear","ceramics"], 18, 0.012),
    ("beryllium", "Beryllium", "BE", "industrial", "rare", 6000_00, 0.40, 0.45, ["metal","industrial","aerospace","nuclear"], 14, 0.018),
    ("meteorite", "Meteorite Iron", "MET", "fantasy", "epic", 100000_00, 0.65, 0.25, ["metal","fantasy","cosmic","collectible"], 10, 0.04),
    ("void_steel", "Void Steel", "VST", "fantasy", "legendary", 750000_00, 0.90, 0.15, ["metal","fantasy","void","mythic"], 7, 0.055),
]


# ============================================================
# ENERGY (30)
# ============================================================
ENERGY_ASSETS = [
    # Real (10)
    ("oil", "Crude Oil", "OIL", "fossil", "common", 80_00, 0.30, 0.95, ["energy","oil","fossil"], 12, 0.018),
    ("brent", "Brent Oil", "BRENT", "fossil", "uncommon", 85_00, 0.30, 0.90, ["energy","oil","fossil","european"], 12, 0.018),
    ("gas", "Natural Gas", "GAS", "fossil", "common", 3_00, 0.50, 0.85, ["energy","gas","fossil"], 10, 0.025),
    ("coal", "Coal", "COAL", "fossil", "common", 130_00, 0.25, 0.85, ["energy","coal","fossil"], 14, 0.012),
    ("nuclear", "Nuclear Energy", "NUC", "nuclear", "rare", 250_00, 0.20, 0.75, ["energy","nuclear","clean"], 16, 0.010),
    ("solar", "Solar Energy", "SOL_E", "renewable", "uncommon", 90_00, 0.30, 0.80, ["energy","solar","renewable","clean"], 12, 0.015),
    ("wind", "Wind Energy", "WIND", "renewable", "uncommon", 75_00, 0.30, 0.80, ["energy","wind","renewable","clean"], 12, 0.015),
    ("hydro", "Hydroelectric", "HYDRO", "renewable", "uncommon", 110_00, 0.18, 0.85, ["energy","hydro","renewable","clean"], 18, 0.010),
    ("geo", "Geothermal", "GEO", "renewable", "rare", 180_00, 0.25, 0.65, ["energy","geo","renewable","clean"], 16, 0.012),
    ("battery", "Battery Cells", "BATT", "tech", "rare", 320_00, 0.45, 0.70, ["energy","battery","ev","tech"], 8, 0.025),

    # Future-tech (5)
    ("hydrogen", "Hydrogen Fuel", "H2", "future", "rare", 450_00, 0.55, 0.50, ["energy","hydrogen","clean","future"], 8, 0.03),
    ("fusion", "Fusion Fuel", "FUS", "future", "epic", 12000_00, 0.85, 0.20, ["energy","fusion","future","mythic"], 6, 0.05),
    ("antimatter", "Antimatter", "AM", "future", "mythic", 1000000_00, 0.95, 0.05, ["energy","antimatter","scifi","mythic"], 4, 0.07),
    ("plasma", "Plasma Cores", "PLZ", "future", "legendary", 80000_00, 0.90, 0.15, ["energy","plasma","scifi","mythic"], 5, 0.06),
    ("dark_energy", "Dark Energy", "DE", "future", "mythic", 5000000_00, 0.95, 0.05, ["energy","cosmic","scifi","mythic"], 4, 0.08),

    # Extra energy (15)
    ("diesel", "Diesel Fuel", "DSL", "fossil", "common", 95_00, 0.30, 0.90, ["energy","oil","transport","fossil"], 12, 0.018),
    ("gasoline", "Gasoline", "GSN", "fossil", "common", 100_00, 0.32, 0.95, ["energy","oil","transport","fossil"], 12, 0.020),
    ("kerosene", "Kerosene", "KER", "fossil", "common", 90_00, 0.30, 0.80, ["energy","oil","aviation","fossil"], 12, 0.018),
    ("ethanol", "Ethanol", "ETH_F", "renewable", "common", 70_00, 0.35, 0.75, ["energy","biofuel","agro","renewable"], 14, 0.018),
    ("biofuel", "Biofuel", "BIO", "renewable", "uncommon", 110_00, 0.35, 0.65, ["energy","biofuel","agro","renewable"], 14, 0.018),
    ("tidal", "Tidal Energy", "TID", "renewable", "rare", 220_00, 0.30, 0.55, ["energy","tidal","renewable","ocean"], 16, 0.014),
    ("lightning", "Lightning Cells", "LTG", "tech", "rare", 480_00, 0.60, 0.55, ["energy","battery","tech"], 7, 0.035),
    ("graphene", "Graphene Cells", "GRP", "tech", "epic", 2200_00, 0.70, 0.40, ["energy","graphene","tech","future"], 6, 0.04),
    ("solid_state", "Solid-State Battery", "SSB", "tech", "epic", 3500_00, 0.65, 0.45, ["energy","battery","tech","future"], 7, 0.038),
    ("capacitor", "Quantum Capacitor", "QCP", "future", "epic", 8500_00, 0.75, 0.35, ["energy","quantum","tech","scifi"], 6, 0.045),
    ("zero_point", "Zero-Point Energy", "ZPE", "future", "mythic", 8000000_00, 0.95, 0.05, ["energy","scifi","mythic"], 4, 0.08),
    ("ion_drive", "Ion Drive Cells", "ION", "future", "epic", 4500_00, 0.75, 0.35, ["energy","scifi","space","tech"], 6, 0.045),
    ("helium3", "Helium-3", "HE3", "future", "legendary", 250000_00, 0.85, 0.10, ["energy","fusion","space","mythic"], 5, 0.06),
    ("void_energy", "Void Energy", "VEN", "future", "legendary", 350000_00, 0.90, 0.10, ["energy","void","scifi","mythic"], 5, 0.07),
    ("stellar_core", "Stellar Core", "SCR", "future", "mythic", 7000000_00, 0.95, 0.05, ["energy","cosmic","scifi","mythic"], 4, 0.08),
]


# ============================================================
# GAMING STOCKS (40)
# ============================================================
STOCK_ASSETS = [
    # IT/Gaming (10)
    ("dragonsoft", "DragonSoft", "DRGN", "it_gaming", "rare", 480_00, 0.45, 0.85, ["stock","gaming","tech"], 12, 0.018),
    ("pixel_motors", "Pixel Motors", "PXM", "it_gaming", "uncommon", 120_00, 0.40, 0.80, ["stock","gaming","ev"], 14, 0.016),
    ("ai_labs", "AI Labs", "AIL", "it_gaming", "epic", 950_00, 0.65, 0.85, ["stock","ai","tech","future"], 8, 0.030),
    ("snake_studios", "Snake Studios", "SNK", "it_gaming", "uncommon", 75_00, 0.50, 0.75, ["stock","gaming","tryll"], 12, 0.020),
    ("titan_forge", "Titan Forge", "TTF", "it_gaming", "rare", 350_00, 0.40, 0.80, ["stock","gaming","industrial"], 12, 0.018),
    ("void_robotics", "Void Robotics", "VOID", "it_gaming", "rare", 280_00, 0.55, 0.75, ["stock","robotics","tech"], 10, 0.025),
    ("neural_sys", "Neural Systems", "NEU", "it_gaming", "epic", 1200_00, 0.55, 0.85, ["stock","ai","tech","future"], 9, 0.025),
    ("quantum_co", "Quantum Computing Co", "QTC", "it_gaming", "epic", 2400_00, 0.65, 0.80, ["stock","quantum","tech","future"], 8, 0.030),
    ("crypto_exch", "Crypto Exchange Inc", "CEXX", "it_gaming", "rare", 180_00, 0.65, 0.85, ["stock","crypto","fintech"], 8, 0.030),
    ("meta_realm", "MetaRealm", "MTR", "it_gaming", "uncommon", 95_00, 0.55, 0.75, ["stock","gaming","metaverse","vr"], 10, 0.025),

    # Finance/Banks (5)
    ("neon_bank", "Neon Bank", "NBK", "finance", "rare", 220_00, 0.30, 0.90, ["stock","finance","bank"], 18, 0.012),
    ("void_capital", "Void Capital", "VCP", "finance", "uncommon", 140_00, 0.40, 0.80, ["stock","finance","bank","void"], 14, 0.018),
    ("insurance_inc", "Insurance Inc", "INS", "finance", "uncommon", 80_00, 0.20, 0.85, ["stock","finance","insurance"], 22, 0.008),
    ("pension_fund", "Pension Fund Co", "PFC", "finance", "uncommon", 45_00, 0.15, 0.80, ["stock","finance","retirement"], 24, 0.006),
    ("chronos_invest", "Chronos Investments", "CHI", "finance", "rare", 310_00, 0.35, 0.85, ["stock","finance","invest"], 16, 0.015),

    # Industry/Mining (5)
    ("orbital_mining", "Orbital Mining", "ORB", "industry", "rare", 180_00, 0.55, 0.65, ["stock","mining","space","industrial"], 10, 0.025),
    ("deep_earth", "Deep Earth Mining", "DEM", "industry", "uncommon", 90_00, 0.35, 0.80, ["stock","mining","industrial"], 14, 0.018),
    ("asteroid_co", "Asteroid Mining Co", "AST", "industry", "epic", 420_00, 0.65, 0.55, ["stock","mining","space","scifi"], 8, 0.035),
    ("cyberfarm", "CyberFarm", "FARM", "industry", "uncommon", 65_00, 0.30, 0.75, ["stock","agro","tech"], 16, 0.012),
    ("robo_logistics", "Robo Logistics", "RBL", "industry", "uncommon", 110_00, 0.40, 0.80, ["stock","robotics","logistics"], 12, 0.018),

    # Special (5)
    ("moon_delivery", "Moon Delivery", "MOOND", "special", "epic", 380_00, 0.65, 0.50, ["stock","logistics","space","scifi"], 8, 0.035),
    ("dragon_inc", "Dragon Inc", "DRG", "special", "legendary", 1500_00, 0.90, 0.30, ["stock","fantasy","mythic"], 6, 0.05),
    ("pixel_pharma", "Pixel Pharma", "PHM", "special", "rare", 240_00, 0.55, 0.65, ["stock","pharma","bio","tech"], 12, 0.022),
    ("neon_records", "Neon Records", "NRC", "special", "uncommon", 55_00, 0.45, 0.65, ["stock","entertainment","music"], 14, 0.020),
    ("void_studios", "Void Studios", "VDS", "special", "rare", 195_00, 0.50, 0.65, ["stock","entertainment","film","void"], 12, 0.022),

    # Extra stocks (15)
    ("cyber_arena", "Cyber Arena", "CYBA", "it_gaming", "rare", 220_00, 0.55, 0.65, ["stock","esports","gaming","entertainment"], 10, 0.025),
    ("holo_tech", "Holo Tech", "HOLO", "it_gaming", "epic", 680_00, 0.65, 0.60, ["stock","tech","ar","vr","future"], 8, 0.030),
    ("byte_motors", "Byte Motors", "BYM", "industry", "rare", 380_00, 0.50, 0.70, ["stock","ev","auto","tech"], 11, 0.025),
    ("aether_corp", "Aether Corp", "AETH", "special", "epic", 1100_00, 0.65, 0.45, ["stock","mythic","mystical","scifi"], 7, 0.038),
    ("nova_games", "Nova Games", "NOVA", "it_gaming", "rare", 280_00, 0.55, 0.70, ["stock","gaming","entertainment"], 10, 0.025),
    ("zenith_studios", "Zenith Studios", "ZNS", "special", "uncommon", 95_00, 0.40, 0.75, ["stock","entertainment","film"], 14, 0.018),
    ("hex_security", "Hex Security", "HXS", "it_gaming", "rare", 320_00, 0.40, 0.75, ["stock","cybersec","tech"], 12, 0.020),
    ("tryll_corp", "Tryll Corp", "TRC", "it_gaming", "epic", 1800_00, 0.55, 0.85, ["stock","tryll","gaming","tech"], 9, 0.025),
    ("phoenix_air", "Phoenix Airlines", "PHA", "industry", "uncommon", 85_00, 0.35, 0.80, ["stock","airline","travel"], 14, 0.018),
    ("ocean_freight", "Ocean Freight Co", "OFC", "industry", "uncommon", 110_00, 0.30, 0.75, ["stock","logistics","shipping"], 16, 0.015),
    ("meta_food", "Meta Food", "MFD", "special", "uncommon", 65_00, 0.30, 0.85, ["stock","food","retail"], 18, 0.012),
    ("gene_labs", "Gene Labs", "GNL", "special", "rare", 290_00, 0.55, 0.65, ["stock","bio","pharma","tech"], 10, 0.028),
    ("skyport", "SkyPort", "SKP", "industry", "uncommon", 125_00, 0.45, 0.70, ["stock","logistics","drone","tech"], 12, 0.020),
    ("mythic_media", "Mythic Media", "MYM", "special", "rare", 320_00, 0.50, 0.65, ["stock","entertainment","media","fantasy"], 11, 0.025),
    ("oblivion_games", "Oblivion Games", "OBL", "it_gaming", "rare", 250_00, 0.60, 0.65, ["stock","gaming","entertainment"], 9, 0.028),
]


# ============================================================
# TECH GIANTS (real-world stocks) (~50)
# ============================================================
TECH_GIANTS_ASSETS = [
    # Tech (10)
    ("tesla", "Tesla", "TSLA", "tech_giant", "epic", 250_00, 0.55, 0.95, ["stock","tech","ev","auto","real"], 8, 0.025),
    ("nvidia", "NVIDIA", "NVDA", "tech_giant", "epic", 950_00, 0.50, 0.95, ["stock","tech","semiconductor","ai","real"], 8, 0.022),
    ("apple", "Apple", "AAPL", "tech_giant", "epic", 220_00, 0.30, 0.95, ["stock","tech","mobile","real"], 12, 0.012),
    ("microsoft", "Microsoft", "MSFT", "tech_giant", "epic", 420_00, 0.28, 0.95, ["stock","tech","software","ai","real"], 12, 0.011),
    ("google", "Alphabet", "GOOGL", "tech_giant", "epic", 165_00, 0.32, 0.95, ["stock","tech","internet","ai","real"], 12, 0.013),
    ("amazon", "Amazon", "AMZN", "tech_giant", "epic", 185_00, 0.32, 0.95, ["stock","tech","retail","cloud","real"], 12, 0.013),
    ("meta", "Meta", "META", "tech_giant", "epic", 520_00, 0.40, 0.95, ["stock","tech","social","metaverse","real"], 10, 0.018),
    ("netflix", "Netflix", "NFLX", "tech_giant", "rare", 700_00, 0.40, 0.90, ["stock","tech","entertainment","media","real"], 11, 0.018),
    ("oracle", "Oracle", "ORCL", "tech_giant", "rare", 145_00, 0.25, 0.90, ["stock","tech","software","cloud","real"], 14, 0.011),
    ("ibm", "IBM", "IBM", "tech_giant", "rare", 220_00, 0.22, 0.90, ["stock","tech","software","ai","real"], 16, 0.010),

    # Auto (8)
    ("ford", "Ford", "F", "auto", "uncommon", 12_00, 0.30, 0.95, ["stock","auto","industrial","real"], 16, 0.013),
    ("gm", "General Motors", "GM", "auto", "uncommon", 50_00, 0.30, 0.95, ["stock","auto","industrial","real"], 16, 0.013),
    ("toyota", "Toyota", "TM", "auto", "rare", 200_00, 0.25, 0.90, ["stock","auto","industrial","real"], 18, 0.011),
    ("bmw", "BMW", "BMW", "auto", "rare", 105_00, 0.30, 0.85, ["stock","auto","luxury","real"], 16, 0.013),
    ("mercedes", "Mercedes-Benz", "MBG", "auto", "rare", 65_00, 0.30, 0.85, ["stock","auto","luxury","real"], 16, 0.013),
    ("ferrari", "Ferrari", "RACE", "auto", "epic", 460_00, 0.30, 0.80, ["stock","auto","luxury","real"], 14, 0.014),
    ("uber", "Uber", "UBER", "tech_giant", "uncommon", 78_00, 0.45, 0.90, ["stock","tech","transport","real"], 10, 0.020),
    ("boeing", "Boeing", "BA", "auto", "rare", 175_00, 0.40, 0.85, ["stock","aerospace","industrial","real"], 12, 0.018),

    # Finance (8)
    ("jpmorgan", "JPMorgan Chase", "JPM", "finance_giant", "epic", 215_00, 0.25, 0.95, ["stock","finance","bank","real"], 18, 0.010),
    ("visa", "Visa", "V", "finance_giant", "epic", 280_00, 0.22, 0.95, ["stock","finance","payments","real"], 18, 0.009),
    ("mastercard", "Mastercard", "MA", "finance_giant", "epic", 480_00, 0.22, 0.95, ["stock","finance","payments","real"], 18, 0.009),
    ("paypal", "PayPal", "PYPL", "finance_giant", "rare", 75_00, 0.40, 0.90, ["stock","finance","fintech","real"], 12, 0.018),
    ("goldman", "Goldman Sachs", "GS", "finance_giant", "rare", 480_00, 0.30, 0.90, ["stock","finance","bank","real"], 16, 0.013),
    ("blackrock", "BlackRock", "BLK", "finance_giant", "epic", 850_00, 0.25, 0.90, ["stock","finance","invest","real"], 18, 0.011),
    ("berkshire", "Berkshire Hathaway", "BRK", "finance_giant", "epic", 615000_00, 0.18, 0.85, ["stock","finance","invest","real"], 24, 0.007),
    ("robinhood", "Robinhood", "HOOD", "finance_giant", "uncommon", 22_00, 0.55, 0.90, ["stock","finance","fintech","real"], 8, 0.025),

    # Consumer (7)
    ("cocacola", "Coca-Cola", "KO", "consumer", "rare", 65_00, 0.18, 0.95, ["stock","consumer","beverage","real"], 24, 0.007),
    ("pepsi", "PepsiCo", "PEP", "consumer", "rare", 170_00, 0.18, 0.95, ["stock","consumer","beverage","food","real"], 24, 0.007),
    ("mcdonalds", "McDonald's", "MCD", "consumer", "rare", 290_00, 0.20, 0.95, ["stock","consumer","food","real"], 22, 0.008),
    ("starbucks", "Starbucks", "SBUX", "consumer", "rare", 95_00, 0.30, 0.90, ["stock","consumer","food","beverage","real"], 16, 0.013),
    ("walmart", "Walmart", "WMT", "consumer", "epic", 80_00, 0.18, 0.95, ["stock","consumer","retail","real"], 24, 0.007),
    ("nike", "Nike", "NKE", "consumer", "rare", 78_00, 0.30, 0.90, ["stock","consumer","fashion","sport","real"], 16, 0.013),
    ("adidas", "Adidas", "ADS", "consumer", "rare", 195_00, 0.30, 0.85, ["stock","consumer","fashion","sport","real"], 16, 0.013),

    # Gaming/Entertainment (7)
    ("disney", "Disney", "DIS", "entertainment_giant", "epic", 95_00, 0.30, 0.95, ["stock","entertainment","media","real"], 14, 0.014),
    ("sony", "Sony", "SONY", "entertainment_giant", "rare", 90_00, 0.32, 0.90, ["stock","entertainment","tech","gaming","real"], 12, 0.015),
    ("nintendo_co", "Nintendo", "NTDOY", "entertainment_giant", "rare", 12_00, 0.40, 0.85, ["stock","gaming","entertainment","real"], 12, 0.018),
    ("ea_games", "Electronic Arts", "EA", "entertainment_giant", "rare", 145_00, 0.35, 0.90, ["stock","gaming","entertainment","real"], 12, 0.017),
    ("activision", "Activision Blizzard", "ATVI", "entertainment_giant", "rare", 95_00, 0.35, 0.90, ["stock","gaming","entertainment","real"], 12, 0.017),
    ("take_two", "Take-Two Interactive", "TTWO", "entertainment_giant", "rare", 165_00, 0.40, 0.85, ["stock","gaming","entertainment","real"], 11, 0.018),
    ("ubisoft", "Ubisoft", "UBI", "entertainment_giant", "uncommon", 25_00, 0.45, 0.80, ["stock","gaming","entertainment","real"], 10, 0.022),

    # AI/Future (5)
    ("openai", "OpenAI", "OAI", "ai_giant", "legendary", 1500_00, 0.65, 0.50, ["stock","ai","tech","future","real"], 6, 0.040),
    ("anthropic", "Anthropic", "ANTH", "ai_giant", "legendary", 1200_00, 0.60, 0.55, ["stock","ai","tech","future","real"], 6, 0.038),
    ("palantir", "Palantir", "PLTR", "ai_giant", "epic", 35_00, 0.55, 0.85, ["stock","ai","data","tech","real"], 8, 0.030),
    ("spacex", "SpaceX", "SPCX", "ai_giant", "legendary", 2200_00, 0.50, 0.50, ["stock","space","aerospace","tech","real"], 8, 0.030),
    ("amd", "AMD", "AMD", "ai_giant", "epic", 165_00, 0.50, 0.95, ["stock","tech","semiconductor","ai","real"], 8, 0.025),

    # Industrial/Energy (5)
    ("exxon", "ExxonMobil", "XOM", "energy_giant", "rare", 115_00, 0.30, 0.95, ["stock","oil","energy","real"], 14, 0.013),
    ("shell", "Shell", "SHEL", "energy_giant", "rare", 70_00, 0.30, 0.90, ["stock","oil","energy","real"], 14, 0.013),
    ("bp", "BP", "BP", "energy_giant", "rare", 38_00, 0.32, 0.85, ["stock","oil","energy","real"], 14, 0.014),
    ("ge", "General Electric", "GE", "industrial_giant", "rare", 165_00, 0.30, 0.90, ["stock","industrial","aerospace","real"], 14, 0.013),
    ("siemens", "Siemens", "SIE", "industrial_giant", "rare", 175_00, 0.28, 0.85, ["stock","industrial","tech","real"], 16, 0.012),
]


# ============================================================
# RARE MATERIALS (35)
# ============================================================
RARE_ASSETS = [
    ("crystal_dust", "Crystal Dust", "CRY", "rare_mat", "rare", 5000_00, 0.55, 0.50, ["rare","crystal","fantasy"], 12, 0.025),
    ("alien_ore", "Alien Ore", "ALN", "rare_mat", "epic", 25000_00, 0.65, 0.40, ["rare","alien","scifi"], 10, 0.035),
    ("dragon_scale", "Dragon Scale", "DSC", "rare_mat", "epic", 50000_00, 0.75, 0.30, ["rare","fantasy","dragon"], 8, 0.040),
    ("void_fragment", "Void Fragment", "VFR", "rare_mat", "legendary", 200000_00, 0.85, 0.20, ["rare","void","fantasy","mythic"], 6, 0.050),
    ("quantum_core", "Quantum Core", "QCR", "rare_mat", "legendary", 350000_00, 0.85, 0.20, ["rare","quantum","scifi","mythic"], 6, 0.050),
    ("ancient_relic", "Ancient Relic", "REL", "rare_mat", "epic", 80000_00, 0.70, 0.30, ["rare","ancient","fantasy"], 9, 0.040),
    ("star_metal", "Star Metal", "SMT", "rare_mat", "legendary", 500000_00, 0.85, 0.15, ["rare","cosmic","fantasy","mythic"], 6, 0.055),
    ("dark_matter", "Dark Matter", "DMA", "rare_mat", "mythic", 5000000_00, 0.95, 0.05, ["rare","cosmic","scifi","mythic"], 4, 0.07),
    ("phoenix_feather", "Phoenix Feather", "PHX", "rare_mat", "mythic", 8000000_00, 0.95, 0.05, ["rare","fantasy","fire","mythic"], 4, 0.07),
    ("time_crystal", "Time Crystal", "TCY", "rare_mat", "mythic", 12000000_00, 0.95, 0.05, ["rare","quantum","scifi","mythic"], 4, 0.075),
    ("nebula_stone", "Nebula Stone", "NBL", "rare_mat", "epic", 95000_00, 0.75, 0.25, ["rare","cosmic","fantasy"], 8, 0.045),
    ("phantom_essence", "Phantom Essence", "PHE", "rare_mat", "legendary", 600000_00, 0.85, 0.15, ["rare","fantasy","mystical","mythic"], 5, 0.060),
    ("soul_shard", "Soul Shard", "SLS", "rare_mat", "legendary", 800000_00, 0.85, 0.10, ["rare","fantasy","mystical","mythic"], 5, 0.060),
    ("god_tear", "God Tear", "GDT", "rare_mat", "mythic", 25000000_00, 0.95, 0.05, ["rare","fantasy","mythic","divine"], 3, 0.080),
    ("astral_dust", "Astral Dust", "ADS", "rare_mat", "rare", 3500_00, 0.55, 0.45, ["rare","cosmic","fantasy"], 12, 0.030),
    ("cosmic_pearl", "Cosmic Pearl", "CMP", "rare_mat", "epic", 120000_00, 0.80, 0.25, ["rare","cosmic","fantasy"], 8, 0.045),
    ("eldritch_bone", "Eldritch Bone", "ELB", "rare_mat", "legendary", 700000_00, 0.85, 0.10, ["rare","fantasy","mystical","mythic"], 5, 0.060),
    ("demon_heart", "Demon Heart", "DMH", "rare_mat", "mythic", 15000000_00, 0.95, 0.05, ["rare","fantasy","mythic","dark"], 3, 0.075),
    ("angel_wing", "Angel Wing", "ANW", "rare_mat", "mythic", 18000000_00, 0.95, 0.05, ["rare","fantasy","mythic","divine"], 3, 0.075),
    ("singularity", "Singularity Fragment", "SNG", "rare_mat", "mythic", 50000000_00, 0.95, 0.03, ["rare","cosmic","scifi","mythic"], 3, 0.080),

    # Extra rare (15)
    ("void_pearl", "Void Pearl", "VDP", "rare_mat", "epic", 95000_00, 0.85, 0.20, ["rare","void","fantasy","mythic"], 6, 0.050),
    ("chaos_shard", "Chaos Shard", "CHS", "rare_mat", "epic", 110000_00, 0.85, 0.20, ["rare","fantasy","mystical","chaos"], 6, 0.050),
    ("order_crystal", "Order Crystal", "ORD", "rare_mat", "epic", 105000_00, 0.55, 0.25, ["rare","fantasy","mystical","order"], 9, 0.030),
    ("life_essence", "Life Essence", "LFE", "rare_mat", "legendary", 450000_00, 0.75, 0.15, ["rare","fantasy","mystical","divine"], 6, 0.045),
    ("death_essence", "Death Essence", "DTH", "rare_mat", "legendary", 500000_00, 0.85, 0.10, ["rare","fantasy","mystical","dark"], 5, 0.055),
    ("frost_crystal", "Frost Crystal", "FRC", "rare_mat", "rare", 8000_00, 0.55, 0.45, ["rare","fantasy","ice","crystal"], 12, 0.025),
    ("ember_stone", "Ember Stone", "EMB", "rare_mat", "rare", 12000_00, 0.65, 0.40, ["rare","fantasy","fire","crystal"], 10, 0.030),
    ("storm_core", "Storm Core", "STC", "rare_mat", "epic", 65000_00, 0.75, 0.30, ["rare","fantasy","storm","crystal"], 8, 0.040),
    ("abyss_ink", "Abyss Ink", "ABS", "rare_mat", "epic", 75000_00, 0.80, 0.25, ["rare","fantasy","abyss","liquid"], 7, 0.045),
    ("sun_fragment", "Sun Fragment", "SUN", "rare_mat", "legendary", 850000_00, 0.85, 0.10, ["rare","cosmic","fantasy","sun"], 5, 0.060),
    ("moon_fragment", "Moon Fragment", "MNF", "rare_mat", "legendary", 750000_00, 0.85, 0.10, ["rare","cosmic","fantasy","moon"], 5, 0.060),
    ("titan_horn", "Titan Horn", "TTH", "rare_mat", "legendary", 950000_00, 0.85, 0.10, ["rare","fantasy","titan","mythic"], 5, 0.060),
    ("kraken_eye", "Kraken Eye", "KRE", "rare_mat", "legendary", 1100000_00, 0.85, 0.08, ["rare","fantasy","kraken","mystic"], 4, 0.065),
    ("ether_dust", "Ether Dust", "ETD", "rare_mat", "rare", 9500_00, 0.65, 0.45, ["rare","fantasy","mystical"], 10, 0.030),
    ("void_rose", "Void Rose", "VRS", "rare_mat", "legendary", 1500000_00, 0.90, 0.07, ["rare","void","fantasy","mythic"], 4, 0.070),
]


# ============================================================
# AGRO / COMMODITIES (25)
# ============================================================
AGRO_ASSETS = [
    ("wheat", "Wheat", "WHE", "grain", "common", 6_00, 0.20, 0.95, ["agro","grain","food"], 24, 0.008),
    ("corn", "Corn", "COR", "grain", "common", 4_50, 0.20, 0.95, ["agro","grain","food"], 24, 0.008),
    ("coffee", "Coffee", "COF", "tropical", "uncommon", 200_00, 0.30, 0.85, ["agro","beverage","tropical"], 16, 0.015),
    ("cocoa", "Cocoa", "COC", "tropical", "uncommon", 7500_00, 0.35, 0.80, ["agro","tropical","food"], 14, 0.018),
    ("sugar", "Sugar", "SUG", "tropical", "common", 30, 0.20, 0.95, ["agro","food"], 22, 0.008),
    ("soybeans", "Soybeans", "SOY", "grain", "common", 12_00, 0.22, 0.90, ["agro","grain","food"], 20, 0.010),
    ("cotton", "Cotton", "COT", "fiber", "common", 90, 0.25, 0.85, ["agro","fiber","textile"], 20, 0.011),
    ("rice", "Rice", "RIC", "grain", "common", 17_00, 0.20, 0.90, ["agro","grain","food"], 24, 0.008),
    ("oj", "Orange Juice", "OJ", "tropical", "uncommon", 4_00, 0.30, 0.80, ["agro","beverage","food"], 14, 0.014),
    ("timber", "Timber", "TIM", "raw", "uncommon", 600_00, 0.25, 0.80, ["agro","wood","construction"], 22, 0.010),
    # Extra agro (15)
    ("barley", "Barley", "BAR", "grain", "common", 5_50, 0.20, 0.85, ["agro","grain","food","beer"], 24, 0.008),
    ("oats", "Oats", "OAT", "grain", "common", 4_00, 0.20, 0.80, ["agro","grain","food"], 24, 0.008),
    ("tea", "Tea", "TEA", "tropical", "uncommon", 350, 0.25, 0.85, ["agro","beverage","tropical"], 22, 0.010),
    ("tobacco", "Tobacco", "TOB", "tropical", "uncommon", 400, 0.30, 0.75, ["agro","tropical"], 18, 0.013),
    ("grapes", "Grapes (Wine)", "GRP_A", "tropical", "uncommon", 12_00, 0.30, 0.75, ["agro","beverage","luxury","wine"], 20, 0.012),
    ("olive", "Olive Oil", "OLV", "tropical", "uncommon", 700_00, 0.30, 0.75, ["agro","food","mediterranean"], 18, 0.013),
    ("honey", "Honey", "HNY", "raw", "rare", 800_00, 0.25, 0.65, ["agro","food","luxury"], 18, 0.012),
    ("cattle", "Cattle (Beef)", "CTL", "livestock", "uncommon", 1800_00, 0.25, 0.85, ["agro","livestock","food"], 20, 0.010),
    ("pork", "Pork (Lean)", "PRK", "livestock", "common", 90_00, 0.30, 0.85, ["agro","livestock","food"], 18, 0.013),
    ("fish", "Fish (Tuna)", "FSH", "seafood", "uncommon", 25_00, 0.30, 0.75, ["agro","seafood","food"], 18, 0.013),
    ("milk", "Dairy (Milk)", "MLK", "livestock", "common", 3_50, 0.18, 0.90, ["agro","livestock","food"], 24, 0.007),
    ("eggs", "Eggs", "EGG", "livestock", "common", 2_50, 0.20, 0.90, ["agro","livestock","food"], 22, 0.008),
    ("apple", "Apple (Fruit)", "APL", "fruit", "common", 1_50, 0.20, 0.85, ["agro","fruit","food"], 20, 0.010),
    ("salt", "Salt", "SLT", "raw", "common", 60, 0.10, 0.95, ["agro","food","essential"], 24, 0.005),
    ("spice", "Spice (Paprika)", "SPC", "raw", "uncommon", 40_00, 0.30, 0.65, ["agro","food","luxury"], 18, 0.012),
]


# ============================================================
# INDEXES (21) — basket of underlying assets, smoother movements
# ============================================================
INDEX_ASSETS = [
    ("crypto10", "Crypto 10 Index", "C10", "basket", "uncommon", 1000_00, 0.50, 0.95, ["index","crypto"], 12, 0.020),
    ("metal_idx", "Metal Index", "MTI", "basket", "uncommon", 1000_00, 0.20, 0.95, ["index","metal"], 24, 0.008),
    ("energy_idx", "Energy Index", "ENX", "basket", "uncommon", 1000_00, 0.30, 0.95, ["index","energy"], 16, 0.013),
    ("tech_idx", "Tech Index", "TCI", "basket", "uncommon", 1000_00, 0.35, 0.95, ["index","tech","stock"], 14, 0.015),
    ("meme_idx", "Meme Index", "MMI", "basket", "rare", 1000_00, 0.85, 0.85, ["index","meme","crypto"], 4, 0.045),
    ("rare_idx", "Rare Materials Index", "RMX", "basket", "rare", 1000_00, 0.70, 0.55, ["index","rare","fantasy"], 8, 0.035),
    ("agro_idx", "Agro Index", "AGI", "basket", "uncommon", 1000_00, 0.22, 0.90, ["index","agro"], 22, 0.009),
    ("gaming_idx", "Gaming Index", "GMI", "basket", "uncommon", 1000_00, 0.40, 0.90, ["index","gaming","tech","stock"], 12, 0.018),
    ("ai_idx", "AI Index", "AII", "basket", "rare", 1000_00, 0.55, 0.90, ["index","ai","tech"], 8, 0.025),
    ("defi_idx", "DeFi Index", "DFI", "basket", "uncommon", 1000_00, 0.60, 0.85, ["index","defi","crypto"], 8, 0.028),
    ("nft_idx", "NFT Index", "NFI", "basket", "rare", 1000_00, 0.80, 0.55, ["index","nft","crypto"], 6, 0.040),
    ("stablecoin_idx", "Stablecoin Index", "STI", "basket", "common", 1000_00, 0.05, 1.0, ["index","stablecoin","crypto"], 24, 0.001),
    ("fintech_idx", "FinTech Index", "FTI", "basket", "uncommon", 1000_00, 0.30, 0.90, ["index","finance","tech"], 16, 0.013),
    ("space_idx", "Space Index", "SPI", "basket", "rare", 1000_00, 0.55, 0.55, ["index","space","scifi"], 8, 0.030),
    ("bio_idx", "Bio Index", "BII", "basket", "rare", 1000_00, 0.50, 0.70, ["index","bio","pharma"], 10, 0.025),
    ("green_idx", "Green Energy Index", "GRI", "basket", "uncommon", 1000_00, 0.30, 0.85, ["index","energy","clean","renewable"], 14, 0.015),
    ("mining_idx", "Mining Index", "MNI", "basket", "uncommon", 1000_00, 0.35, 0.80, ["index","mining","metal"], 14, 0.015),
    ("fantasy_idx", "Fantasy Index", "FNI", "basket", "epic", 1000_00, 0.85, 0.40, ["index","fantasy","mythic"], 6, 0.045),
    ("robo_idx", "Robotics Index", "RBI", "basket", "rare", 1000_00, 0.50, 0.80, ["index","robotics","tech"], 10, 0.025),
    ("void_idx", "Void Index", "VDI", "basket", "epic", 1000_00, 0.90, 0.30, ["index","void","fantasy","mythic"], 5, 0.055),
    ("mythic_idx", "Mythic Index", "MYI", "basket", "legendary", 1000_00, 0.95, 0.25, ["index","mythic","fantasy","cosmic"], 4, 0.065),
]


# ============================================================
# Combined catalog (294 assets)
# ============================================================
def all_assets() -> list[tuple]:
    """Return [(category, key, name, symbol, subcategory, rarity, base_price,
    volatility, liquidity, tags, cycle_h, amplitude), ...]"""
    out = []
    for spec in CRYPTO_ASSETS:
        out.append(("crypto", *spec))
    for spec in METAL_ASSETS:
        out.append(("metals", *spec))
    for spec in ENERGY_ASSETS:
        out.append(("energy", *spec))
    for spec in STOCK_ASSETS:
        out.append(("stocks", *spec))
    for spec in TECH_GIANTS_ASSETS:
        out.append(("tech", *spec))
    for spec in RARE_ASSETS:
        out.append(("rare", *spec))
    for spec in AGRO_ASSETS:
        out.append(("agro", *spec))
    for spec in INDEX_ASSETS:
        out.append(("indexes", *spec))
    return out


def get_asset_by_key(key: str) -> dict | None:
    for entry in all_assets():
        if entry[1] == key:
            cat, k, name, symbol, sub, rarity, base, vol, liq, tags, cyc_h, amp = entry
            return {
                "category": cat, "key": k, "name": name, "symbol": symbol,
                "subcategory": sub, "rarity": rarity, "base_price": base,
                "volatility": vol, "liquidity": liq, "tags": tags,
                "cycle_period_h": cyc_h, "cycle_amplitude": amp,
            }
    return None
