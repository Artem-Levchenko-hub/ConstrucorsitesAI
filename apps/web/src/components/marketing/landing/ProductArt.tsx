/**
 * Рисунки товаров для превью приложений.
 *
 * Раньше здесь были градиентные пятна: коричневый круг на коричневом квадрате.
 * Они честно занимали место, но выдавали заготовку — человек видел заглушку и
 * переставал верить, что перед ним настоящее приложение. А превью на витрине
 * должно работать наоборот: вызывать «хочу такое же себе».
 *
 * Ракурс выбран по узнаваемости, а не по красоте. Вид сверху на чашку давал
 * аккуратное кольцо, которое читалось как логотип, а не как кофе; поэтому кофе
 * нарисован сбоку — стакан с крышкой и держателем, чашка на блюдце. У круассана
 * вертикальные надрезы: без них полумесяц читается как радуга.
 *
 * Правило рисунка: две-три краски на предмет, никаких теней и бликов. Задача —
 * узнаваемость за полсекунды на картинке размером с ноготь, а не натюрморт.
 */

export type ArtKind =
  | "cappuccino"
  | "flatwhite"
  | "croissant"
  | "cheesecake"
  | "monstera"
  | "ficus"
  | "zamioculcas"
  | "calathea";

/** Мягкая подложка под предметом — своя у каждого, чтобы ряд не выглядел одинаковым. */
const TINT: Record<ArtKind, string> = {
  cappuccino: "#f0e8de",
  flatwhite: "#f3ede4",
  croissant: "#f7eeda",
  cheesecake: "#f5efe5",
  monstera: "#e7f0e5",
  ficus: "#ebf1e7",
  zamioculcas: "#e6eeeb",
  calathea: "#eff2e5",
};

/**
 * Горшок — общий для всех растений.
 *
 * Лист сам по себе читался как значок: зелёный кружок на палочке. Горшок сразу
 * говорит, что это ТОВАР из магазина растений, а не иллюстрация к слову
 * «природа», и разом отличает карточку от иконки.
 */
function Pot() {
  return (
    <>
      <path d="M20.4 30.2h23.2a1.4 1.4 0 0 1 1.4 1.6l-.3 2.2a1.4 1.4 0 0 1-1.4 1.2H20.7a1.4 1.4 0 0 1-1.4-1.2l-.3-2.2a1.4 1.4 0 0 1 1.4-1.6Z" fill="#a9755a" />
      <path d="M21 35.6h22l-2 9.2a2 2 0 0 1-2 1.6H25a2 2 0 0 1-2-1.6l-2-9.2Z" fill="#c08a63" />
    </>
  );
}

const ART: Record<ArtKind, React.JSX.Element> = {
  cappuccino: (
    <>
      <path d="M21 9h22a1.6 1.6 0 0 1 1.6 1.9l-.5 2.6a1.6 1.6 0 0 1-1.6 1.3H21.5a1.6 1.6 0 0 1-1.6-1.3l-.5-2.6A1.6 1.6 0 0 1 21 9Z" fill="#59381f" />
      <path d="M22 15h20l-2.4 24.4a2.2 2.2 0 0 1-2.2 2H26.6a2.2 2.2 0 0 1-2.2-2L22 15Z" fill="#f6efe4" />
      <path d="M23.3 23h17.4l-.9 9H24.2l-.9-9Z" fill="#a9703c" />
      <path d="M26.6 27.5h11" stroke="#f6efe4" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  flatwhite: (
    <>
      <path d="M45 24c3.4 0 5.6 1.9 5.6 4.6S48.4 33 45 33" stroke="#cbbfae" strokeWidth="2.6" fill="none" strokeLinecap="round" />
      <path d="M17 19h28v8.5C45 34 39.3 38.5 31 38.5S17 34 17 27.5V19Z" fill="#fbf7f1" />
      <ellipse cx="31" cy="19.4" rx="14" ry="3.4" fill="#8f5c2e" />
      <ellipse cx="31" cy="19.4" rx="9" ry="2.1" fill="#c79a68" />
      <ellipse cx="31" cy="42" rx="19" ry="3.4" fill="#ded3c2" />
    </>
  ),
  croissant: (
    <>
      <path d="M11 35c0-10.2 9-17.6 21-17.6S53 24.8 53 35c0 1.8-2 2.8-3.6 1.8-1.8-1.1-3.6-2.7-4.8-4.3-2.8 2.6-7.2 4-12.6 4s-9.8-1.4-12.6-4c-1.2 1.6-3 3.2-4.8 4.3C13 37.8 11 36.8 11 35Z" fill="#dba55c" />
      <path d="M23 20.6v11.2M32 18.8v13.4M41 20.6v11.2" stroke="#b97c33" strokeWidth="1.7" strokeLinecap="round" />
    </>
  ),
  cheesecake: (
    <>
      <path d="M17 33h30v4.6a2.4 2.4 0 0 1-2.4 2.4H19.4a2.4 2.4 0 0 1-2.4-2.4V33Z" fill="#c08f4f" />
      <path d="M17 33 47 33 47 17.4 17 23.6Z" fill="#fbf1dc" />
      <path d="M17 23.6 47 17.4 47 21 17 27.2Z" fill="#f2ddb4" />
      <circle cx="41" cy="14.4" r="3.4" fill="#b44a63" />
      <path d="M41 10.8v-2.2" stroke="#6f8f63" strokeWidth="1.6" strokeLinecap="round" />
    </>
  ),
  monstera: (
    <>
      <Pot />
      <path d="M32 6.6v23.6" stroke="#7f9a76" strokeWidth="2" strokeLinecap="round" />
      <path d="M32 29.2c-9.8-1.4-16.2-7.4-16.2-14.6C15.8 7.8 22.8 3.2 32 3.2s16.2 4.6 16.2 11.4c0 7.2-6.4 13.2-16.2 14.6Z" fill="#4f8a55" />
      <path d="M32 5.2v23.6" stroke="#e7f0e5" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M16 9.6 29.6 8.4 16.4 12.2Z" fill="#e7f0e5" />
      <path d="M15.6 16.2 29.6 15.4 16.4 18.8Z" fill="#e7f0e5" />
      <path d="M18.8 22.2 29.6 21.8 20.8 24.8Z" fill="#e7f0e5" />
      <path d="M48 9.6 34.4 8.4 47.6 12.2Z" fill="#e7f0e5" />
      <path d="M48.4 16.2 34.4 15.4 47.6 18.8Z" fill="#e7f0e5" />
      <path d="M45.2 22.2 34.4 21.8 43.2 24.8Z" fill="#e7f0e5" />
    </>
  ),
  ficus: (
    <>
      <Pot />
      <path d="M32 30.4V7" stroke="#7f9a76" strokeWidth="2" strokeLinecap="round" />
      <ellipse cx="32" cy="5.6" rx="4.6" ry="3" fill="#55925c" />
      <ellipse cx="23.4" cy="11" rx="6.4" ry="3.4" fill="#55925c" transform="rotate(-24 23.4 11)" />
      <ellipse cx="40.6" cy="11" rx="6.4" ry="3.4" fill="#55925c" transform="rotate(24 40.6 11)" />
      <ellipse cx="22.6" cy="19.4" rx="6.8" ry="3.6" fill="#4b8553" transform="rotate(-20 22.6 19.4)" />
      <ellipse cx="41.4" cy="19.4" rx="6.8" ry="3.6" fill="#4b8553" transform="rotate(20 41.4 19.4)" />
      <ellipse cx="25.4" cy="26.6" rx="5.8" ry="3.2" fill="#55925c" transform="rotate(-14 25.4 26.6)" />
      <ellipse cx="38.6" cy="26.6" rx="5.8" ry="3.2" fill="#55925c" transform="rotate(14 38.6 26.6)" />
    </>
  ),
  zamioculcas: (
    <>
      <Pot />
      <path d="M24 30.4C21.6 22 22.4 13.8 26.4 5.6" stroke="#5f8f6b" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <path d="M40 30.4c2.4-8.4 1.6-16.6-2.4-24.8" stroke="#5f8f6b" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <path d="M25.6 9.6c3.4-2.4 6.6-1.8 8 1.4-2.6 2.8-5.8 2.6-8-1.4Zm-1.6 7c3.4-2.4 6.6-1.8 8 1.4-2.6 2.8-5.8 2.6-8-1.4Zm-1 7.2c3.4-2.4 6.6-1.8 8 1.4-2.6 2.8-5.8 2.6-8-1.4Z" fill="#3f7a57" />
      <path d="M38.4 9.6c-3.4-2.4-6.6-1.8-8 1.4 2.6 2.8 5.8 2.6 8-1.4Zm1.6 7c-3.4-2.4-6.6-1.8-8 1.4 2.6 2.8 5.8 2.6 8-1.4Zm1 7.2c-3.4-2.4-6.6-1.8-8 1.4 2.6 2.8 5.8 2.6 8-1.4Z" fill="#4c8a63" />
    </>
  ),
  calathea: (
    <>
      <Pot />
      <path d="M32 30.4c-3-3.6-4.6-7.4-5-11.6M32 30.4c3-3.6 4.6-7.4 5-11.6" stroke="#7f9a76" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <ellipse cx="21.6" cy="16.6" rx="5.4" ry="10.4" fill="#5f8d42" transform="rotate(-26 21.6 16.6)" />
      <ellipse cx="42.4" cy="16.6" rx="5.4" ry="10.4" fill="#5f8d42" transform="rotate(26 42.4 16.6)" />
      <ellipse cx="32" cy="13.4" rx="5.8" ry="11" fill="#6d9a48" />
      <path d="M32 3.4v20M27.4 10.6c2 1 3.4 2.2 4.6 3.8M36.6 10.6c-2 1-3.4 2.2-4.6 3.8M28 17.2c1.8.8 3 1.8 4 3M36 17.2c-1.8.8-3 1.8-4 3" stroke="#dbe8c6" strokeWidth="1.5" fill="none" strokeLinecap="round" />
    </>
  ),
};

export function ProductArt({ kind }: { kind: ArtKind }) {
  return (
    <span className="ys-art" style={{ background: TINT[kind] }} aria-hidden="true">
      <svg viewBox="0 0 64 48" role="presentation" focusable="false">
        {ART[kind]}
      </svg>
    </span>
  );
}
