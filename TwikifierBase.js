// create a function scope (this is closed in another file)
var createWikifier = function(window, jQuery, passedServerOptions) {
    var serverOptions = {
        'host': 'http://tiddlyspace.com',
        'container': null
    };
    if (typeof passedServerOptions === 'undefed') {
        passedServerOptions = {};
    }
    jQuery.extend(serverOptions, passedServerOptions);

/*** psuedo-globals required to get rolling ***/

// XXX TiddlyWiki requires a document global, rather than
// being passed that document in startup.
var document = window.document;

// XXX TiddlyWiki requires a store global, rather than it
// being passed where needed.
var store = new TiddlyWiki();

// XXX The findContainingTiddler method on story is required for
// invokeMacro to process, but we don't actually need it to really
// do anything.
var story = {
    findContainingTiddler: function(e) { return e; }
};

// XXX the highlightHack variable is used by the function 
// highlightify which is rather irrelevant in this context
// annoyingly it is used in Story.js and Macros.js but defined in main.js
var highlightHack = null;

// provide a stubbed alert in case something calls it
function alert(e) {
    console.log(e);
}

// config.browser (used in FormatterHelpers) requires navigator
var navigator = { userAgent: "twikifier" };  
