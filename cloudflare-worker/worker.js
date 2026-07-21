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
          "[즉석 검색]\n" +
          "  검색 키워드              — 전국 (병렬, ~10분)\n" +
          "  검색 키워드/지역         — 지정 지역\n" +
          "  검색 더블알엘 모자/서울  — 예시\n" +
          "  ※ 지역은 반드시 / 뒤에. 없으면 전부 키워드.\n\n" +
          "[상시 감시 키워드 관리]\n" +
          "  상시목록                 — 현재 감시 키워드 보기\n" +
          "  상시등록 키워드          — 감시 추가\n" +
          "  상시삭제 키워드          — 감시 삭제\n" +
          "  (상시등록 키워드 삭제 도 삭제로 동작)\n\n" +
          "(앞에 / 를 붙여도 됩니다. 검색 끝나면 새 매물만 알림.)"
        );
        return new Response("OK");
      }

      // ── 상시 감시 키워드 관리 (keywords.json 직접 편집) ──
      const wm = text.trim().match(/^\/?상시(등록|삭제|목록)\s*([\s\S]*)$/);
      if (wm) {
        let action = wm[1];                 // 등록 | 삭제 | 목록
        let arg = (wm[2] || "").trim();
        // "상시등록 키워드 삭제" 형태도 삭제로 처리
        if (action === "등록" && /\s*삭제$/.test(arg)) {
          action = "삭제";
          arg = arg.replace(/\s*삭제$/, "").trim();
        }
        await manageKeywords(env, chatId, action, arg);
        return new Response("OK");
      }

      // ── 즉석 검색 ──
      const am = text.trim().match(/^\/?(?:검색|search)\s+([\s\S]+)$/);
      if (!am) return new Response("OK");
      const argStr = am[1].trim();

      // 키워드와 지역은 '/' 로 구분(키워드 공백 허용).
      let keyword, region;
      const sl = argStr.indexOf("/");
      if (sl >= 0) {
        keyword = argStr.slice(0, sl).trim();
        region = argStr.slice(sl + 1).trim();
      } else {
        keyword = argStr;
        region = "";
      }
      if (!keyword) return new Response("OK");

      // 지역 있으면 단일(search.yml), 없으면 전국 병렬(search-parallel.yml).
      let workflow, inputs, eta;
      if (region) {
        workflow = "search.yml";
        inputs = { keyword, region };
        eta = "";
      } else {
        workflow = "search-parallel.yml";
        inputs = { keyword };
        eta = "\n※ 전국 병렬검색 — ~10분 걸려요.";
      }

      const resp = await ghDispatch(env, workflow, inputs);
      if (resp.ok) {
        await sendMsg(env, chatId,
          `🔍 검색 시작\n` +
          `  키워드: ${keyword}\n` +
          `  지역: ${region || "전국"}\n\n` +
          `끝나면 결과 알림.` + eta
        );
      } else {
        const err = (await resp.text()).slice(0, 300);
        await sendMsg(env, chatId, `❌ 검색 실행 실패 (HTTP ${resp.status})\n${err}`);
      }
      return new Response("OK");
    }
  };

  // ── GitHub Actions 워크플로 디스패치 ──
  function ghDispatch(env, workflow, inputs) {
    return fetch(
      `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: ghHeaders(env),
        body: JSON.stringify({ ref: "main", inputs }),
      }
    );
  }

  function ghHeaders(env) {
    return {
      "Authorization": `Bearer ${env.GH_PAT}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "daangn-bot-webhook",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    };
  }

  // ── 감시 키워드(keywords.json) 조회/추가/삭제 ──
  async function manageKeywords(env, chatId, action, keyword) {
    // 현재 keywords.json 읽기
    let file;
    try {
      const r = await fetch(
        `https://api.github.com/repos/${env.GH_REPO}/contents/keywords.json?ref=main`,
        { headers: ghHeaders(env) }
      );
      if (!r.ok) {
        await sendMsg(env, chatId, `❌ keywords.json 읽기 실패 (HTTP ${r.status})`);
        return;
      }
      file = await r.json();
    } catch (e) {
      await sendMsg(env, chatId, `❌ keywords.json 읽기 오류: ${e}`);
      return;
    }

    // base64 → UTF-8 → 배열
    let list;
    try {
      const bytes = Uint8Array.from(atob((file.content || "").replace(/\n/g, "")), c => c.charCodeAt(0));
      list = JSON.parse(new TextDecoder().decode(bytes));
      if (!Array.isArray(list)) list = [];
    } catch (e) {
      list = [];
    }

    const listBody = () => (list.length
      ? "📋 상시 감시 키워드 (" + list.length + "개)\n" + list.map(k => "  • " + k).join("\n") +
        "\n\n추가: 상시등록 키워드\n삭제: 상시삭제 키워드"
      : "📋 상시 감시 키워드가 없습니다.\n상시등록 키워드 로 추가하세요.");

    // 상시목록, 또는 키워드 없이 상시등록/상시삭제만 친 경우 → 목록 표시
    if (action === "목록" || !keyword) {
      await sendMsg(env, chatId, listBody());
      return;
    }

    const exists = list.some(k => k === keyword);
    let newList, verb;
    if (action === "등록") {
      if (exists) {
        await sendMsg(env, chatId, `ℹ️ "${keyword}" 는 이미 감시 중입니다.`);
        return;
      }
      newList = list.concat([keyword]);
      verb = "등록";
    } else { // 삭제
      if (!exists) {
        await sendMsg(env, chatId, `ℹ️ "${keyword}" 는 감시 목록에 없습니다.\n상시목록 으로 확인하세요.`);
        return;
      }
      newList = list.filter(k => k !== keyword);
      verb = "삭제";
    }

    // 새 내용 커밋 (base64 UTF-8)
    const newJson = JSON.stringify(newList, null, 2) + "\n";
    const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(newJson)));
    const put = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/contents/keywords.json`,
      {
        method: "PUT",
        headers: ghHeaders(env),
        body: JSON.stringify({
          message: `감시 키워드 ${verb}: ${keyword} (텔레그램봇)`,
          content: b64,
          sha: file.sha,
          branch: "main",
        }),
      }
    );

    if (put.ok) {
      const body = `✅ 감시 키워드 ${verb}: "${keyword}"\n\n` +
        "📋 현재 (" + newList.length + "개)\n" + newList.map(k => "  • " + k).join("\n") +
        "\n\n다음 자동 감시(매일 09/14/20시)부터 반영됩니다.";
      await sendMsg(env, chatId, body);
    } else {
      const err = (await put.text()).slice(0, 300);
      await sendMsg(env, chatId, `❌ ${verb} 실패 (HTTP ${put.status})\n${err}`);
    }
  }

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
