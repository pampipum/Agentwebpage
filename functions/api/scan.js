function safeUrl(input) {
  try {
    const parsed = new URL(input);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function scoreReport(html, url) {
  const findings = [];
  const quickWins = [];
  let score = 100;

  const lower = html.toLowerCase();

  const hasTitle = /<title>[^<]{4,}<\/title>/.test(lower);
  const hasMetaDescription = /<meta[^>]+name=["']description["'][^>]+content=["'][^"']{20,}/.test(lower);
  const hasForm = /<form\b/.test(lower);
  const hasTelLink = /href=["']tel:/.test(lower);
  const hasMailto = /href=["']mailto:/.test(lower);
  const hasBookingHint = /(book|reservation|appointment|quote|schedule)/.test(lower);

  if (!hasTitle) {
    score -= 12;
    findings.push('Missing clear page title signal for discoverability.');
    quickWins.push('Add a strong, intent-driven title for core service pages.');
  }

  if (!hasMetaDescription) {
    score -= 10;
    findings.push('Meta description is weak or missing.');
    quickWins.push('Add conversion-focused meta descriptions to improve click quality.');
  }

  if (!hasForm) {
    score -= 18;
    findings.push('No visible lead-capture form detected.');
    quickWins.push('Deploy a frictionless intake form with auto-response workflow.');
  }

  if (!hasTelLink && !hasMailto) {
    score -= 15;
    findings.push('No direct contact action (phone/email) detected in page markup.');
    quickWins.push('Expose one-tap contact actions in hero and footer.');
  }

  if (!hasBookingHint) {
    score -= 10;
    findings.push('No booking/quote intent flow detected.');
    quickWins.push('Add booking or quote CTA with structured intake fields.');
  }

  if (url.startsWith('http://')) {
    score -= 10;
    findings.push('Site is using HTTP instead of HTTPS.');
    quickWins.push('Force HTTPS for trust, conversion confidence, and SEO resilience.');
  }

  if (findings.length === 0) {
    findings.push('No major structural issues found in surface scan; deeper workflow audit recommended.');
    quickWins.push('Run workflow-level audit for booking, lead response, and follow-up automation.');
  }

  score = Math.max(25, Math.min(100, score));

  return {
    score,
    summary:
      'Nai One completed a surface diagnostic. This report prioritizes opportunities with direct operational and revenue impact.',
    findings,
    quickWins,
  };
}

async function sendLeadWebhook(env, payload) {
  if (!env.LEAD_WEBHOOK_URL) {
    return;
  }

  try {
    await fetch(env.LEAD_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    // Swallow webhook failures so scan still returns.
  }
}

export async function onRequestPost(context) {
  const { request, env } = context;

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: 'Invalid JSON body.' }, { status: 400 });
  }

  const businessUrl = safeUrl(body.businessUrl || '');
  const name = String(body.name || '').trim();
  const email = String(body.email || '').trim();

  if (!businessUrl || !name || !email) {
    return Response.json(
      { error: 'businessUrl, name, and email are required.' },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);

  let html = '';
  try {
    const res = await fetch(businessUrl, {
      method: 'GET',
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        'User-Agent': 'NaiOneDiagnosticBot/1.0 (+https://attikonlab.uk)',
      },
    });

    html = await res.text();
  } catch {
    clearTimeout(timeout);
    return Response.json(
      { error: 'Unable to fetch business URL for diagnostic.' },
      { status: 422 }
    );
  }

  clearTimeout(timeout);

  const report = scoreReport(html, businessUrl);

  const leadPayload = {
    source: 'attikonlab-nai-one-scan',
    timestamp: new Date().toISOString(),
    lead: { name, email, businessUrl },
    report,
    userAgent: request.headers.get('user-agent') || '',
    ipHint: request.headers.get('cf-connecting-ip') || '',
  };

  await sendLeadWebhook(env, leadPayload);

  return Response.json({ ok: true, report });
}
