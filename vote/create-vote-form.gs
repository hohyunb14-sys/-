/**
 * 권역모임 지원금 활용 건의 — 구글 익명투표 자동 생성 스크립트
 *
 * ▶ 사용법 (PC 권장, 1분)
 *   1. https://script.google.com 접속 → [새 프로젝트]
 *   2. 원래 있던 코드를 모두 지우고, 이 파일 내용을 전부 붙여넣기 → 저장(Ctrl+S)
 *   3. 위쪽 함수 선택 칸에서 createVoteForm 선택 → [실행]
 *   4. 처음 한 번 권한 승인 창이 뜸
 *      → 내 구글 계정 선택 → [고급] → [(프로젝트 이름)(으)로 이동] → [허용]
 *   5. 아래 [실행 로그]에 나온 "응답 링크"를 단톡방에 공유
 *
 * ▶ 마감 후
 *   - closeVote   : 응답 받기 종료
 *   - checkResult : 동의/거절 집계 + 결론을 실행 로그에 출력
 */

// ===== 필요하면 여기만 고치세요 =====
const CONFIG = {
  title: '권역모임 지원금 활용 건의 — 익명 의견 수렴',
  recipient: '정성용 팀장님',
  amount: '50만원',
  sender: '권역모임회 일동',
  deadline: '', // 예: '11월 12일(목) 18시까지' — 비워두면 표시하지 않음
};
// ====================================

const AGREE = '동의합니다 — 모임 지원금으로 전달';
const REFUSE = '거절합니다 — 모임 지원금 사용 반대 (자율 모금으로 진행)';

function createVoteForm() {
  const c = CONFIG;
  const form = FormApp.create(c.title);

  // ── 안내문 ──
  const desc = [
    '권역모임 지원금 활용방안 건의 내용입니다.',
    '',
    `현재 ${c.recipient} 자녀 진료 문제로 행사 순번 진입이 어려운 상황입니다.`,
    `모임 참여 중인 팀원분들께서 소정의 금액을 지원해 드리면 어떻겠냐는 건의를 주셔서, ` +
      `모임 지원금 ${c.amount}을 '${c.sender}' 명의로 전달하는 방안을 생각하고 있습니다.`,
    '',
    '다만 모임의 원칙상 지원금 용도와 맞지 않을 수 있어,',
    '▶ 한 분이라도 거절 의사가 있으시면 모임 지원금은 사용하지 않고, 별도로 자율적으로 모아서 전달하겠습니다.',
    '',
    '🔒 이 투표는 익명입니다. 이메일·이름 등 응답자 정보는 수집되지 않으며, ' +
      '만든 사람도 누가 어떻게 응답했는지 알 수 없습니다. 편하게 의견 주세요.',
  ];
  if (c.deadline) desc.push('', `⏰ 응답 마감: ${c.deadline}`);
  form.setDescription(desc.join('\n'));

  // ── 질문 1: 동의 / 거절 (필수) ──
  const vote = form.addMultipleChoiceItem();
  vote
    .setTitle(`모임 지원금 ${c.amount}을 '${c.sender}' 명의로 ${c.recipient}께 전달하는 것에 대해 의견을 선택해 주세요.`)
    .setHelpText("'거절'이 1표라도 나오면 모임 지원금 대신 자율 모금으로 진행합니다.")
    .setChoices([vote.createChoice(AGREE), vote.createChoice(REFUSE)])
    .setRequired(true);

  // ── 질문 2: 기타 의견 (선택) ──
  form
    .addParagraphTextItem()
    .setTitle('기타 의견 (선택)')
    .setHelpText('자유롭게 남겨 주세요. 이 칸도 익명입니다.')
    .setRequired(false);

  // ── 익명 설정 ──
  // 이메일 수집 안 함 (새 방식 → 안 되면 예전 방식)
  try {
    form.setEmailCollectionType(FormApp.EmailCollectionType.DO_NOT_COLLECT);
  } catch (e) {
    try { form.setCollectEmail(false); } catch (e2) {}
  }
  // '1회로 제한'을 켜면 구글 로그인이 필요해서 익명으로 느껴지지 않음 → 끔.
  // (중복 응답이 있어도 '거절 1표 이상이면 자율 모금'이라 결론은 바뀌지 않음)
  form.setLimitOneResponsePerUser(false);
  try { form.setRequireLogin(false); } catch (e) {} // 회사(Workspace) 계정에서만 의미 있음
  form.setAllowResponseEdits(false);
  form.setShowLinkToRespondAgain(false);
  form.setPublishingSummary(false); // 결과는 만든 사람이 단톡방에 공유
  form.setProgressBar(false);
  form.setConfirmationMessage('소중한 의견 감사합니다. 결과는 단톡방으로 공유드리겠습니다.');

  // 2025년 이후 새 설문은 '게시'가 필요할 수 있음
  try { if (typeof form.setPublished === 'function') form.setPublished(true); } catch (e) {}
  try { form.setAcceptingResponses(true); } catch (e) {}

  PropertiesService.getScriptProperties().setProperty('FORM_ID', form.getId());

  let link = form.getPublishedUrl();
  try { link = form.shortenFormUrl(link); } catch (e) {}

  Logger.log('✅ 익명투표가 만들어졌습니다.');
  Logger.log('📨 응답 링크 (단톡방 공유용): ' + link);
  Logger.log('✏️ 수정·결과 확인 링크 (본인만): ' + form.getEditUrl());
}

function closeVote() {
  const form = openVoteForm_();
  form.setAcceptingResponses(false);
  form.setCustomClosedFormMessage('투표가 마감되었습니다. 참여해 주셔서 감사합니다.');
  Logger.log('🔒 응답 받기를 종료했습니다.');
}

function checkResult() {
  const form = openVoteForm_();
  const responses = form.getResponses();
  let agree = 0;
  let refuse = 0;
  const comments = [];

  responses.forEach(function (r) {
    r.getItemResponses().forEach(function (ir) {
      const type = ir.getItem().getType();
      const answer = String(ir.getResponse() || '').trim();
      if (type === FormApp.ItemType.MULTIPLE_CHOICE) {
        if (answer.indexOf('동의') === 0) agree++;
        else if (answer.indexOf('거절') === 0) refuse++;
      } else if (type === FormApp.ItemType.PARAGRAPH_TEXT && answer) {
        comments.push(answer);
      }
    });
  });

  Logger.log(`📊 총 응답 ${responses.length}명 · 동의 ${agree} · 거절 ${refuse}`);
  Logger.log(
    refuse > 0
      ? `➡️ 거절 ${refuse}표 → 모임 지원금은 사용하지 않고, 자율 모금으로 진행합니다.`
      : `➡️ 거절 0표 → 모임 지원금 ${CONFIG.amount}을 '${CONFIG.sender}' 명의로 전달합니다.`
  );
  if (comments.length) {
    Logger.log('💬 기타 의견');
    comments.forEach(function (t, i) { Logger.log(`  ${i + 1}. ${t}`); });
  }
}

function openVoteForm_() {
  const id = PropertiesService.getScriptProperties().getProperty('FORM_ID');
  if (!id) throw new Error('먼저 createVoteForm 을 실행해 주세요.');
  return FormApp.openById(id);
}
