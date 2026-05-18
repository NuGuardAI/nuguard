import { test, expect } from '@playwright/test';

test.describe('Healthcare Voice Agent Tests', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', msg => console.log('PAGE LOG:', msg.text()));

    // Mock SpeechRecognition API before the page loads
    await page.addInitScript(() => {
      class MockSpeechRecognition {
        constructor() {
          this.onresult = null;
          this.onend = null;
          this.onerror = null;
          this.onstart = null;
          this.continuous = false;
          this.interimResults = false;
          this.lang = 'en-US';
          window.__mockRecognition = this;
        }
        start() {
          console.log('Mock SpeechRecognition started');
          if (this.onstart) setTimeout(() => this.onstart(), 50);
        }
        stop() {
          console.log('Mock SpeechRecognition stopped');
          if (this.onend) setTimeout(() => this.onend(), 50);
        }
      }
      window.SpeechRecognition = window.webkitSpeechRecognition = MockSpeechRecognition;
      
      // Also mock SpeechSynthesis to avoid issues in headless mode
      window.speechSynthesis = {
        speak: () => {},
        cancel: () => {},
        pause: () => {},
        resume: () => {},
        getVoices: () => [],
      };
    });
  });

  test('should login and interact via simulated voice input', async ({ page }) => {
    // 1. Navigate to the app
    await page.goto('/');

    // 2. Perform Login
    await page.fill('input[placeholder="Email address"]', 'john@google.com');
    await page.fill('input[placeholder="Password"]', 'user2');
    await page.click('button:has-text("Log In")');

    // 3. Verify Landing on Dashboard
    await expect(page).toHaveURL(/.*dashboard/);
    await expect(page.locator('h2')).toContainText('Welcome');

    // 4. Navigate to AI Assistant
    await page.click('button:has-text("Talk to AI Assistant")');
    await expect(page).toHaveURL(/.*assistant/);

    // 5. Connect Voice
    const connectButton = page.locator('button:has-text("Connect Voice")');
    await connectButton.click();
    
    // Check if the status changed (MIC ACTIVE badge appears)
    await expect(page.locator('text=MIC ACTIVE')).toBeVisible();

    // 6. Simulate Voice Transcript: "I have some chest pain"
    await page.evaluate((text) => {
      const recognition = window.__mockRecognition;
      if (recognition && recognition.onresult) {
        // Construct event that matches what UserContext.jsx expects (e.results[i].isFinal)
        const event = {
          results: [
            {
              0: { transcript: text },
              isFinal: true,
              length: 1
            }
          ],
          resultIndex: 0,
          length: 1
        };
        recognition.onresult(event);
      }
    }, 'I have some chest pain');

    // 7. Verify the transcript appears in the chat
    await expect(page.locator('text=I have some chest pain')).toBeVisible();

    // Give React a moment to propagate the state to the disconnect function
    await page.waitForTimeout(1000);

    // 8. Wait for AI response and Disconnect
    const disconnectButton = page.locator('button:has-text("Disconnect & Analyze")');
    await disconnectButton.click();

    // Log any errors if we don't navigate
    const errorBanner = page.locator('.error-banner');
    if (await errorBanner.isVisible()) {
        const errorText = await errorBanner.innerText();
        console.error('Error during disconnect:', errorText);
    }

    // 9. Wait for navigation to Recommendation page
    await expect(page).toHaveURL(/.*recommendation/, { timeout: 30000 });
    
    // 10. Verify specialist recommendation heading
    await expect(page.locator('text=Consult Recommendation')).toBeVisible({ timeout: 15000 });
    
    // 11. Check for Prognosis Research
    await expect(page.locator('text=Possible Prognosis')).toBeVisible();
    
    // 12. Verify Doctors list is fetched
    await expect(page.locator('text=Specialization').first()).toBeVisible();
  });
});
