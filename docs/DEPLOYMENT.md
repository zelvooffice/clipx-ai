# Пускане на CLIPX AI — стъпка по стъпка (безплатно: само GitHub + Cloudflare)

Всичко работи в **един Cloudflare Worker** на безплатния план: сайтът, API-то, cron задачата (всяка минута),
базата данни **D1**, хранилището за видео **R2** и **Workers AI** за субтитрите. Кодът е в GitHub.
Не ти трябва сървър, компютър, който да е включен, нито друга услуга.

> Видеото се монтира **в браузъра**. Когато има нови клипове, отвори сайта в един таб и го остави отворен.
> Табът сам ги монтира (≈2 минути за 30-секунден клип). Ако няма отворен таб, клиповете чакат.

---

## 1. Cloudflare акаунт и Zero Trust (еднократно, ~3 мин.)

1. Влез в **dash.cloudflare.com**. Регистрацията е безплатна.
2. В лявото меню отвори **Zero Trust**. Избери име на екипа (например `clipx`) и плана **Free** ($0, до 50 потребители).
   Така се създава адресът `https://<име>.cloudflareaccess.com`, през който става входът с еднократен код по имейл.
3. **Workers & Pages** → ако те пита, избери поддомейн за `workers.dev` (например `clipx`).

## 2. API token за качване на сайта (~2 мин.)

**My Profile → API Tokens → Create Token → Custom token**, с тези права за **акаунта**:

| Право | Ниво |
|---|---|
| Workers Scripts | Edit |
| D1 | Edit |
| Workers R2 Storage | Edit |
| Workers AI | Read |
| Access: Apps and Policies | Edit |
| Access: Organizations, Identity Providers, and Groups | Edit |
| Account Settings | Read |

Дай token-а **само през защитената форма**, която Claude ще ти покаже. Не го пиши в чата.
(Или го сложи в GitHub, виж т. 7.)

## 3. Първо качване (Claude го прави вместо теб)

С token-а се изпълнява:

```bash
cd apps/web
npm ci
npm run build
npx cf deploy --prebuilt --secrets-file <файл с генерирани SESSION_SECRET и TOKEN_ENCRYPTION_KEY>
```

- D1 базата `clipx` и R2 bucket-ът `clipx-media` се създават автоматично.
- Сайтът е на `https://clipx-ai.<поддомейн>.workers.dev`.
- **Докато Access не е настроен, сайтът не пуска никого** (връща 503). Нищо не е изложено.

## 4. Защита на входа: Cloudflare Access + парола при всяко влизане

1. Настройване на Access (Claude го прави с token-а):

   ```bash
   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
   WEB_DOMAIN=clipx-ai.<поддомейн>.workers.dev AUTHORIZED_EMAILS="ти@пример.com" \
   infra/cloudflare/setup-access.sh
   ```

   Скриптът извежда `CF_ACCESS_TEAM_DOMAIN` и `CF_ACCESS_AUD`. Те и `AUTHORIZED_EMAILS` се записват като тайни на Worker-а.
2. Проверка, че без вход нищо не се отваря:

   ```bash
   WEB_DOMAIN=clipx-ai.<поддомейн>.workers.dev infra/cloudflare/verify-access.sh
   ```

3. **Ти:** отвори сайта → въведи имейла си → въведи кода от пощата.
4. **Първо влизане:** сайтът иска да създадеш **паролата на таблото** (поне 12 знака). Пази се само нейният хеш.
   След това паролата се иска **при всяко ново влизане и във всеки нов таб, за всеки потребител**.
   Смяна: Settings → Security → Change dashboard password (всички други отворени сесии се заключват).

## 5. Twitch (~5 мин.)

1. **dev.twitch.tv/console** → **Register Your Application**
   - Name: `CLIPX AI` · Category: `Application Integration` · Client type: **Confidential**
   - OAuth Redirect URL: `https://clipx-ai.<поддомейн>.workers.dev/api/oauth/twitch/callback`
     (точният адрес е в Settings → Scheduler & system)
2. Копирай **Client ID** и натисни **New Secret**.
3. Cloudflare → **Workers & Pages → clipx-ai → Settings → Variables and Secrets** → редактирай
   `TWITCH_CLIENT_ID` и `TWITCH_CLIENT_SECRET` (тип **Secret**) → **Deploy**.
4. В CLIPX: **Settings → Twitch**.
   - **Channel editor** или **Lacy (broadcaster)** → Connect. Без това клиповете само се показват, видеото не може да се вземе законно.
   - Лейси трябва да те направи editor в Twitch (Creator Dashboard → Community → Roles → Editor) или сама да натисне Connect.

## 6. TikTok (~10 мин. + одобрение от TikTok)

1. **developers.tiktok.com** → Manage apps → **Connect an app**.
2. Добави продуктите **Login Kit** и **Content Posting API** (включи **Direct Post**).
   Scopes: `user.info.basic`, `video.publish`, `video.upload`, `video.list`.
3. Redirect URI: `https://clipx-ai.<поддомейн>.workers.dev/api/oauth/tiktok/callback/`.
4. В Cloudflare (както в т. 5.3) въведи `TIKTOK_CLIENT_KEY` и `TIKTOK_CLIENT_SECRET`.
5. CLIPX → **Settings → TikTok → Connect TikTok account**.
6. Докато TikTok не одобри приложението (**audit**), публикациите могат да са само „Only me“ (частни).
   Затова подай заявка за audit от портала. Когато я одобрят, включи **Publishing → TikTok app audited**.

## 6½. Качване без GitHub Actions (както е пуснат сега)

`npm run deploy:r2` (с `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` и `NODE_USE_ENV_PROXY=1` зад прокси)
качва статичните файлове в R2 (`static/…`) и Worker-а без Workers static assets. Worker-ът сервира файловете от R2.
Използва се, когато качването на static assets е блокирано (например зад прокси, който подменя Authorization).

## 7. Качване на кода от GitHub (по желание)

Сайтът се качва от GitHub Actions при всеки push в `main`, след като тестовете минат.
GitHub → репото → **Settings → Secrets and variables → Actions → New repository secret**:

- `CLOUDFLARE_API_TOKEN`: token-ът от т. 2.
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare → **Workers & Pages** → вдясно **Account ID** → Copy.

Настройките на Access и списъкът с имейли **не са в GitHub** (репото е публично). Те са тайни на Worker-а в Cloudflare.
Ръчно пускане: **Actions → Deploy to Cloudflare → Run workflow**.

## Ограничения на безплатния план (честно)

- **Моментите идват от клиповете на зрителите**, подредени по гледания. Няма анализ на звука и картината в реално време.
  За това е нужен платен сървър, а `services/api` е готов за такъв.
- **Видеото се монтира в отворен таб.** Без отворен таб клиповете чакат на опашка.
- **Workers AI:** около 10 000 неврона на ден безплатно, тоест над 200 минути аудио. Стига за 25 клипа на стрийм.
- **R2:** 10 GB безплатно. Един 30-секунден клип заема около 15–25 MB (изходник + монтиран).
- **TikTok:** за публични постове е нужно одобрение (audit). Всеки Direct Post се потвърждава с един клик,
  защото TikTok изисква ръчен избор на поверителност. Черновите в inbox могат да са автоматични.
