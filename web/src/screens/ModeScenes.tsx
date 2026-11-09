// ONB-4: живі сценки до карток «Шукаю житло» / «Здаю житло». Ілюстрації — оригінальний
// маскот Домовика у векторі (assets/scenes/*.svg); анімації (хода, дим, світло у вікнах)
// закладені в самі SVG і вимикаються при «зменшеному русі».

import landlordScene from "../assets/scenes/landlord.svg";
import tenantScene from "../assets/scenes/tenant.svg";

/** Орендар: з клунком на палиці йде лісовою стежкою до міста. */
export function TenantScene() {
  return <img className="mode-scene" src={tenantScene} alt="" aria-hidden="true" draggable={false} />;
}

/** Власник: медовий Домовик махає біля свого будиночка — з комина дим, у вікнах світло. */
export function LandlordScene() {
  return <img className="mode-scene" src={landlordScene} alt="" aria-hidden="true" draggable={false} />;
}
