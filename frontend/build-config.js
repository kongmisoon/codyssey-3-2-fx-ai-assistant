/**
 * build-config.js — Vercel 빌드 단계에서 실행되어 public/config.js 를 생성한다.
 *
 * 왜 필요한가?
 *   정적 HTML/JS 는 브라우저에서 실행되므로 서버의 환경변수(process.env)를 직접 읽을 수 없다.
 *   그래서 빌드할 때 환경변수 API_BASE_URL 값을 config.js 파일 안에 적어 넣는다.
 *   → 백엔드 주소가 바뀌어도 코드 수정 없이 Vercel 환경변수만 바꾸고 재배포하면 된다.
 *
 * 로컬에서 직접 실행:
 *   API_BASE_URL=https://example.onrender.com node build-config.js
 *   (환경변수 없이 로컬에서 실행하면 기존 config.js 를 그대로 둔다)
 */
const fs = require("fs");
const path = require("path");

const target = path.join(__dirname, "public", "config.js");
const raw = (process.env.API_BASE_URL || "").trim().replace(/\/+$/, "");
const onVercel = Boolean(process.env.VERCEL);

if (!raw) {
  if (onVercel) {
    // 배포본이 localhost 를 가리키는 사고를 막기 위해 빌드를 실패시킨다.
    console.error("[build-config] API_BASE_URL 환경변수가 없습니다. Vercel 프로젝트 설정 → Environment Variables 에 추가하세요.");
    process.exit(1);
  }
  console.log("[build-config] API_BASE_URL 이 없어 기존 public/config.js 를 그대로 사용합니다.");
  process.exit(0);
}

let url;
try {
  url = new URL(raw);
} catch {
  console.error(`[build-config] API_BASE_URL 이 올바른 URL 이 아닙니다: ${raw}`);
  process.exit(1);
}
if (!["http:", "https:"].includes(url.protocol)) {
  console.error(`[build-config] API_BASE_URL 은 http(s) 주소여야 합니다: ${raw}`);
  process.exit(1);
}
if (onVercel && url.protocol !== "https:") {
  console.error("[build-config] 배포 환경에서는 https 주소를 사용해야 합니다 (브라우저가 혼합 콘텐츠를 차단).");
  process.exit(1);
}

const content = `// 빌드 시 build-config.js 가 자동 생성한 파일입니다. 직접 수정하지 마세요.
window.APP_CONFIG = Object.freeze(${JSON.stringify({ API_BASE_URL: raw }, null, 2)});
`;
fs.writeFileSync(target, content, "utf8");
console.log(`[build-config] public/config.js 생성 완료 → API_BASE_URL=${raw}`);
