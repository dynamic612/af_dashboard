/**
 * PM2 config for standalone dashboard.
 * From affine-cortex dir: pm2 start standalone_dashboard/ecosystem.config.cjs
 * Or: pm2 start ecosystem.config.cjs --cwd /root/affine-cortex
 */
module.exports = {
  apps: [
    {
      name: 'standalone-dashboard',
      cwd: require('path').resolve(__dirname, '..'),
      script: 'python',
      args: '-m standalone_dashboard.run',
      interpreter: 'none',
      env: { PORT: '5000' },
      env_production: { PORT: '5000' },
    },
  ],
};
