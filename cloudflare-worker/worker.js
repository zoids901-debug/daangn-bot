export default {
    async fetch(request, env) {
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
      if (env.WEBHOOK_SECRET && secret !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      if (request.method !== "POST") return new Response("daangn-bot webhook OK");

      const update = await request.json();
      const text = update.message?.text;
      const chatId = update.message?.chat?.id;
      if (!text || !chatId) return new Response("OK");

      if (/^\/?(?:start|help|도움|도움말)/.test(text)) {
        await sendMsg(env, chatId,
          "🥕 당근 검색봇\n\n" +
          "사용법:\n" +
          "  검색 키워드            — 전국\n" +
          "  검색 키워드 지역       — 지정 지역\n" +
          "  검색 더블알엘 광주     — 예시\n\n" +
          "(앞에 / 를 붙여 /검색 으로 써도 됩니다)\n" +
          "검색 끝나면 새 매물만 알림.\n" +
          "지역 비우면 전국 (2~3시간 걸림)."
        );
        return new Response("OK");
      }

      const m = text.trim().match(/^\/?(?:검색|search)\s+(\S+)(?:\s+(.+))?$/);
      if (!m) return new Response("OK");

      const keyword = m[1];
      const region = (m[2] || "").trim();

      const resp = await fetch(
        `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/search.yml/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GH_PAT}`,
            "Accept": "application/vnd.github+json",
            "User-Agent": "daangn-bot-webhook",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ref: "main", inputs: { keyword, region } }),
        }
      );

      if (resp.ok) {
        await sendMsg(env, chatId,
          `🔍 검색 시작\n` +
          `  키워드: ${keyword}\n` +
          `  지역: ${region || "전국"}\n\n` +
          `끝나면 결과 알림.` +
          (region ? "" : "\n※ 전국은 2~3시간 걸려요.")
        );
      } else {
        const err = (await resp.text()).slice(0, 300);
        await sendMsg(env, chatId, `❌ 검색 실행 실패 (HTTP ${resp.status})\n${err}`);
      }
      return new Response("OK");
    }
  };

  async function sendMsg(env, chatId, text) {
    await fetch(
      `https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
      }
    );
  }
