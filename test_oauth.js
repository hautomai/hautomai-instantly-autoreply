const http = require('http');
const https = require('https');
const url = require('url');

// Just check if localhost:8080 is running or test hitting the auth URL
const authUrl = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=798274399182-3e8hnnt1liv8p6ton400oul16cik7u0b.apps.googleusercontent.com&redirect_uri=http%3A%2F%2Flocalhost%3A8080%2F&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.modify&state=1MKNpojesA89d63Hpc5PAv32cHAAQh&code_challenge=fhH5aqCgHc6Lxtnb0kiSpb3C3bQCMgbdKJg6Qgph6Tk&code_challenge_method=S256&access_type=offline";

https.get(authUrl, (res) => {
  console.log('StatusCode:', res.statusCode);
  console.log('Headers:', res.headers);
  let data = '';
  res.on('data', chunk => data += chunk);
  res.on('end', () => {
    console.log('Body length:', data.length);
    if (data.includes('signin') || data.includes('identifier') || res.statusCode === 302 || res.statusCode === 200) {
      console.log('Successfully reached Google OAuth endpoint.');
    }
  });
}).on('error', err => {
  console.error('Error:', err);
});
