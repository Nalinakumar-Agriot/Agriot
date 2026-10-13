const path = require('path');
const { src, dest, series, parallel } = require('gulp');
const sass = require('gulp-dart-sass');
const autoprefixer = require('gulp-autoprefixer');
const gulpIf = require('gulp-if');
const sourcemaps = require('gulp-sourcemaps');
const browserSync = require('browser-sync').create();
const webpack = require('webpack');
const log = require('fancy-log');
const colors = require('ansi-colors');
const rename = require('gulp-rename');

module.exports = (conf, srcGlob) => {
  // -------------------------------
  // CSS BUILD (FIXED)
  // -------------------------------
  const buildCssTask = function () {
    return src(srcGlob('**/*.scss', '!**/_*.scss'))
      .pipe(gulpIf(conf.sourcemaps, sourcemaps.init()))

      .pipe(
        sass({
          includePaths: [
            path.resolve(process.cwd(), 'node_modules')
          ]
        }).on('error', sass.logError)
      )

      .pipe(gulpIf(conf.sourcemaps, sourcemaps.write()))

      .pipe(
        rename(function (file) {
          file.dirname = file.dirname.replace('scss', 'css');
        })
      )

      .pipe(dest(conf.distPath))
      .pipe(browserSync.stream());
  };

  // -------------------------------
  // AUTOPREFIX CSS
  // -------------------------------
  const buildAutoprefixCssTask = function () {
    return src(conf.distPath + '/css/**/*.css')
      .pipe(
        gulpIf(
          conf.sourcemaps,
          sourcemaps.init({ loadMaps: true })
        )
      )
      .pipe(autoprefixer())
      .pipe(gulpIf(conf.sourcemaps, sourcemaps.write()))
      .pipe(dest(conf.distPath + '/css'))
      .pipe(browserSync.stream());
  };

  // -------------------------------
  // JS BUILD
  // -------------------------------
  const buildJsTask = function (cb) {
    setTimeout(() => {
      webpack(require('../webpack.config'), (err, stats) => {
        if (err) {
          log(colors.red(err.stack || err));
          return cb();
        }

        const info = stats.toJson();

        if (stats.hasErrors()) {
          info.errors.forEach(e =>
            log(colors.red('Webpack error:'), e)
          );
        }

        if (stats.hasWarnings()) {
          info.warnings.forEach(w =>
            log(colors.yellow('Webpack warning:'), w)
          );
        }

        log(
          stats.toString({
            colors: colors.enabled,
            hash: false,
            timings: false,
            chunks: false,
            modules: false,
            children: true,
            version: true
          })
        );

        cb();
        browserSync.reload();
      });
    }, 1);
  };

  // -------------------------------
  // FONTS TASK
  // -------------------------------
  const FONT_TASKS = [
    {
      name: 'flags',
      path: 'node_modules/flag-icons/flags/**/*'
    }
  ].map(font => {
    const taskFunction = function () {
      return src(font.path)
        .pipe(dest(path.join(conf.distPath, 'fonts', font.name)));
    };

    return taskFunction;
  });

  const KATEX_FONT_TASK = [
    {
      name: 'katex',
      path: 'node_modules/katex/dist/fonts/*'
    }
  ].map(font => {
    const taskFunction = function () {
      return src(font.path)
        .pipe(dest(path.join(conf.distPath, 'libs/quill/fonts')));
    };

    return taskFunction;
  });

  const buildFontsTask = parallel(FONT_TASKS, KATEX_FONT_TASK);

  // -------------------------------
  // COPY TASK
  // -------------------------------
  const buildCopyTask = function () {
    return src(
      srcGlob(
        '**/*.png',
        '**/*.gif',
        '**/*.jpg',
        '**/*.jpeg',
        '**/*.svg',
        '**/*.swf',
        '**/*.eot',
        '**/*.ttf',
        '**/*.woff',
        '**/*.woff2'
      )
    ).pipe(dest(conf.distPath));
  };

  // -------------------------------
  // ICONIFY TASK
  // -------------------------------
  const buildIconifyTask = function (cb) {
    const fs = require('fs');
    const { spawn } = require('child_process');

    const directories = ['./fonts/iconify', './assets/vendor/fonts'];

    directories.forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });

    const iconify = spawn('node', ['./fonts/iconify/iconify.js']);

    iconify.stdout.on('data', data => console.log(data.toString()));
    iconify.stderr.on('data', data => console.error(data.toString()));

    iconify.on('close', () => cb());
  };

  // -------------------------------
  // ALL TASKS
  // -------------------------------
  const buildAllTask = series(
    buildCssTask,
    buildJsTask,
    buildFontsTask,
    buildCopyTask,
    buildIconifyTask
  );

  // -------------------------------
  // EXPORTS
  // -------------------------------
  return {
    css: series(buildCssTask, buildAutoprefixCssTask),
    js: buildJsTask,
    fonts: buildFontsTask,
    copy: buildCopyTask,
    iconify: buildIconifyTask,
    all: buildAllTask
  };
};