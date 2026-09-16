// 프론트엔드 설정 — API 서버 주소
//
// 이 파일은 "로컬 개발용 기본값"이다.
// Vercel 에 배포할 때는 build-config.js 가 환경변수 API_BASE_URL 로 이 파일을 새로 만들어 덮어쓴다.
// (정적 사이트는 브라우저에서 process.env 를 읽을 수 없기 때문에, 빌드 시점에 값을 파일로 구워 넣는다)
window.APP_CONFIG = Object.freeze({
  API_BASE_URL: "http://127.0.0.1:8000",
});
