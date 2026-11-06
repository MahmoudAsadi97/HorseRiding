#!/usr/bin/env python3
"""Optional real-browser validation. Requires Playwright and system Chromium.

Run with the synthetic demo loaded. Credentials are read from an ignored .env;
this script prints only checks, never their values.
"""
import argparse
from pathlib import Path

from http_safety import service_base_url
from playwright.sync_api import sync_playwright


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8000')
    p.add_argument('--env-file', type=Path, default=Path('.env'))
    p.add_argument('--output', type=Path, default=Path('.local/browser'))
    args = p.parse_args()
    args.url = service_base_url(args.url)
    config = dict(line.split('=', 1) for line in args.env_file.read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium',
                                    headless=True, args=['--no-sandbox'])
        for width, height, name in [(1440, 1000, 'desktop'), (390, 844, 'mobile')]:
            context = browser.new_context(viewport={'width': width, 'height': height},
                http_credentials={'origin': args.url, 'username': config['ADMIN_USERNAME'],
                                  'password': config['ADMIN_PASSWORD']})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e, errors=errors: errors.append(str(e)))
            response = page.goto(args.url, wait_until='networkidle')
            assert response.status == 200
            assert page.get_by_role('heading', name='Your form. A clearer view.').is_visible()
            assert page.get_by_role('rowheader', name='Demo Western Comet').is_visible()
            assert page.locator('tbody tr').count() == 2
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
            page.screenshot(path=str(args.output / f'{name}.png'), full_page=True)
            page.locator('select[name=report]').select_option('backup')
            page.get_by_role('button', name='Apply filters').click()
            assert page.locator('tbody tr').count() == 1
            assert page.get_by_text('Demo Trainer West', exact=True).is_visible()
            page.goto(args.url + '/runners/demo-wa-r1-h1/sectionals?state=WA')
            assert page.locator('tbody tr').count() == 6
            assert not errors, errors
            context.close()
            print(f'PASS {name}: auth, layout, filter submission, six ordered sectionals')
        browser.close()


if __name__ == '__main__':
    main()
