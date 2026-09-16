// Ícone por ticker (ex: BTC -> .../btc.svg), servido pelo pacote estático
// "cryptocurrency-icons" via jsdelivr -- sem API key, sem chamada extra por
// ativo. Cobre as moedas grandes; tokens menores/mais novos que o pacote não
// tem (comum na carteira do Ivan: FET, SUI, FLOKI) caem pro ícone genérico
// via onError no componente cliente (ver wallet-grid.tsx).
const ICON_CDN = "https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color";

export function coinIconUrl(asset: string): string {
  return `${ICON_CDN}/${asset.toLowerCase()}.svg`;
}

export const GENERIC_COIN_ICON = `${ICON_CDN}/generic.svg`;
