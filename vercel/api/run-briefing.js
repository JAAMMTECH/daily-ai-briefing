const DISPATCH_URL =
  "https://api.github.com/repos/JAAMMTECH/daily-ai-briefing/actions/workflows/daily-briefing.yml/dispatches";

export async function GET(request) {
  const secret = process.env.CRON_SECRET;
  if (!secret || request.headers.get("authorization") !== `Bearer ${secret}`) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  // scheduled=true makes the workflow skip when it is outside 7:00 to noon in
  // Toronto or the day's briefing already went out. That is how only one of
  // the two UTC crons sends across daylight saving time.
  const github = await fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "daily-ai-briefing-timer",
    },
    body: JSON.stringify({ ref: "main", inputs: { scheduled: "true" } }),
  });

  if (github.status !== 204) {
    const detail = await github.text();
    console.error(`GitHub dispatch failed: ${github.status} ${detail}`);
    return Response.json({ error: "dispatch failed", status: github.status }, { status: 502 });
  }
  console.log("Started the daily briefing workflow");
  return Response.json({ ok: true });
}
