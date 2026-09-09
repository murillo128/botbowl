appDirectives.directive('displayMessage', function() {
	return {
		restrict: 'E',
		scope: {
        	messageType: '=type',
        	message: '=data'
      	},
		template: '<div class="alert {{messageType}}">{{message}}</div>',
		link: function (scope, element, attributes) {
            scope.$watch(attributes, function (value) {
            	console.log(attributes);
            	console.log(value);
            	console.log(element[0]);
                element[0].children.hide(); 
            });
        }
	}
});
// Text stays in the DOM and each complete value has its own flexible column.
appDirectives.directive('playerAttributes', function() {
    return {
        restrict: 'E',
        scope: {player: '='},
        template: '<dl aria-label="Player attributes">' +
            '<div><dt>MA</dt><dd aria-label="Movement remaining: {{ movement() }}">{{ movement() }}</dd></div>' +
            '<div><dt>ST</dt><dd aria-label="Strength: {{ player.st }}">{{ player.st }}</dd></div>' +
            '<div><dt>AG</dt><dd aria-label="Agility: {{ player.ag }}">{{ player.ag }}</dd></div>' +
            '<div><dt>AV</dt><dd aria-label="Armour: {{ player.av }}">{{ player.av }}</dd></div></dl>',
        link: function(scope) {
            scope.movement = function() {
                const remaining = scope.player.ma - scope.player.state.moves;
                return remaining < 0 ? '+' + (-remaining) : remaining;
            };
        }
    };
});

appDirectives.directive('bbSquare', function() {
    return {
        link: function(scope, element, attrs) {
            element.attr('role', 'button');
            scope.$watch(function() {
                const square = scope.$eval(attrs.bbSquare);
                if (!square) return '';
                const player = square.player;
                const label = player ? player.name + ', number ' + player.nr +
                    ', MA ' + player.ma + ', ST ' + player.st + ', AG ' + player.ag + ', AV ' + player.av :
                    'Square ' + square.x + ', ' + square.y;
                return [label, !!square.selected, !!(player || scope.getAvailable(square))];
            }, function(values) {
                element.attr('aria-label', values[0]);
                element.attr('aria-pressed', String(values[1]));
                element.attr('tabindex', values[2] ? '0' : '-1');
            }, true);
            function keydown(event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    element.triggerHandler('click');
                }
            }
            element.on('keydown', keydown);
            scope.$on('$destroy', function() { element.off('keydown', keydown); });
        }
    };
});
