// Невеликий набір лінійних іконок (24×24, stroke = currentColor). Без зовнішніх бібліотек.

type IconName =
  | "home"
  | "building"
  | "shield"
  | "user"
  | "chevron"
  | "back"
  | "check"
  | "doc"
  | "plus"
  | "search"
  | "key"
  | "alert"
  | "trash"
  | "edit"
  | "send"
  | "swap"
  | "more"
  | "close"
  | "clock"
  | "star"
  | "photo"
  | "pen"
  | "link"
  | "upload"
  | "menu"
  | "info"
  | "minus"
  | "meter";

const PATHS: Record<IconName, string> = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  building: "M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 9h4a1 1 0 0 1 1 1v11M3 21h18M8 8h3M8 12h3M8 16h3",
  shield: "M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6zM9 12l2 2 4-4",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  chevron: "m9 6 6 6-6 6",
  back: "M15 6l-6 6 6 6",
  check: "m5 12.5 4.5 4.5L19 7.5",
  doc: "M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM14 3v5h5M9 13h6M9 17h6",
  plus: "M12 5v14M5 12h14",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  key: "M14 10a4 4 0 1 0-1.2 2.8L20 20M17 17l2-2M15 15l1.5-1.5",
  alert: "M12 4 2.5 20h19zM12 10v4M12 17.5v.01",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  send: "M4 12 20 4l-6 16-2.5-6.5z",
  swap: "M7 7h12l-3-3M17 17H5l3 3",
  close: "M6 6l12 12M18 6 6 18",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5V12l3 2",
  star: "m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z",
  photo: "M4 6h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM3 16l5-5 4 4 3-3 6 6M15.5 9.5v.01",
  pen: "M4 20l1-4L16 5l3 3L8 19zM14 7l3 3",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  menu: "M4 7h16M4 12h16M4 17h16",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8v.01",
  minus: "M5 12h14",
  meter: "M4.5 17a8.5 8.5 0 1 1 15 0M12 13.5l3.5-4.5M12 13.5v.01M7.5 9.5l.01.01M12 7.5v.01M16.5 9.5v.01",
  more: "M6.5 12a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM13.5 12a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM20.5 12a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z",
};

export function Icon({ name, size = 22 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export type { IconName };
