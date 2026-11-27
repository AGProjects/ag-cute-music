// What the user does, in the log (release-logs.sh, ReactNativeJS): one line an action, "[ui] <what> <detail>".

export function ui(what: string, detail?: unknown) {
  const text =
    detail === undefined
      ? ''
      : typeof detail === 'string'
      ? ` ${detail}`
      : ` ${JSON.stringify(detail)}`;
  console.log(`[ui] ${what}${text}`);
}
