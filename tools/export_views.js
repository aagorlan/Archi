/*
 * Выгрузка всех представлений (View) текущей модели в PDF средствами самого Archi.
 *
 * Скрипт для плагина jArchi (нужна версия 1.7+: в ней появился
 * $.model.renderViewToPDF с опциями). Картинка получается ровно такая же,
 * как в редакторе, — рисует её сам Archi.
 *
 * Запуск (см. tools/archi_export_views.sh, он делает это для всех моделей):
 *
 *   Archi -application com.archimatetool.commandline.app -consoleLog -nosplash \
 *         --loadModel "Склады/WMS.archimate" \
 *         --script.runScript tools/export_views.js
 *
 * Имя файла — по правилу репозитория: <проект>_<имя представления>.pdf,
 * где «проект» — папка, в которой лежит сама модель. Файл кладётся туда же.
 * Правило: docs/05-Правило-PDF-представлений.md
 *
 * После выгрузки зафиксируйте представления в манифесте:
 *   python3 tools/archi_export_pdf.py --adopt
 */

// textAsShapes: false — текст остаётся текстом (поиск и копирование работают),
// embedFonts: true — шрифт кладётся внутрь PDF, чтобы вид не «поехал» на чужой машине.
// Если в вашей сборке шрифт модели недоступен и подписи выглядят иначе,
// поставьте textAsShapes: true — текст превратится в кривые, вид будет точным.
var OPTIONS = { textAsShapes: false, embedFonts: true, textOffsetWorkaround: false };

var File = Java.type('java.io.File');
var System = Java.type('java.lang.System');
var FileOutputStream = Java.type('java.io.FileOutputStream');
var OutputStreamWriter = Java.type('java.io.OutputStreamWriter');

// Путь журнала выгрузки: в него построчно пишутся созданные PDF, чтобы
// tools/archi_export_pdf.py --adopt отметил в манифесте ровно их и ничего лишнего.
var LOG_PATH = System.getenv('ARCHI_EXPORT_LOG');

function safeName(name) {
    var out = String(name === null || name === undefined ? '' : name)
        .replace(/[\/\\:*?"<>|\r\n\t]/g, '-')
        .replace(/^[\s.]+|[\s.]+$/g, '');
    return out.length ? out : 'View';
}

(function () {
    var currentModel = $.model;
    if (!currentModel) {
        console.error('Модель не загружена: добавьте --loadModel перед --script.runScript');
        return;
    }
    var path = currentModel.getPath();
    if (!path) {
        console.error('Модель не сохранена в файл, некуда класть представления');
        return;
    }

    var dir = new File(path).getAbsoluteFile().getParentFile();
    var project = dir.getName();

    var views = $('view');
    if (views.size() === 0) {
        views = $('archimate-diagram-model');
    }

    var log = LOG_PATH ? new OutputStreamWriter(new FileOutputStream(LOG_PATH, true), 'UTF-8')
                       : null;
    var count = 0;
    var failed = 0;
    views.each(function (view) {
        var target = new File(dir, project + '_' + safeName(view.name) + '.pdf');
        try {
            currentModel.renderViewToPDF(view, target.getAbsolutePath(), OPTIONS);
            console.log('+ ' + target.getAbsolutePath());
            if (log) {
                log.write(target.getAbsolutePath() + '\n');
            }
            count++;
        } catch (e) {
            failed++;
            console.error('! ' + view.name + ': ' + e);
            if (failed === 1) {
                console.error('  (renderViewToPDF с опциями есть в jArchi 1.7 и новее — '
                              + 'проверьте версию плагина)');
            }
        }
    });

    if (log) {
        log.close();
    }

    console.log(project + ': выгружено представлений — ' + count
                + (failed ? ', с ошибкой — ' + failed : ''));
})();
