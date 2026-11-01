// Бесплатный «прокси-переводчик» для бота на Google Apps Script.
// 1) Открой https://script.google.com → «Новый проект» → вставь этот код вместо старого.
// 2) Замени MY_SECRET на любую свою секретную строку (буквы и цифры) — её же впишешь в Render как GAS_KEY.
// 3) «Начать развёртывание» → «Новое развёртывание» → тип «Веб-приложение»:
//      Выполнять от имени: Я;   У кого есть доступ: Все.   → «Развернуть» → разреши доступ.
// 4) Скопируй «URL веб-приложения» (заканчивается на /exec) — впиши в Render как GAS_URL.
var SECRET = 'MY_SECRET';

function doGet(e) {
  var p = e.parameter || {};
  var out = ContentService.createTextOutput().setMimeType(ContentService.MimeType.JSON);
  if (p.key !== SECRET) { return out.setContent(JSON.stringify({error: 'forbidden'})); }
  if (!p.q || !p.s || !p.t) { return out.setContent(JSON.stringify({error: 'bad request'})); }
  try {
    return out.setContent(JSON.stringify({t: LanguageApp.translate(p.q, p.s, p.t)}));
  } catch (err) {
    return out.setContent(JSON.stringify({error: String(err)}));
  }
}
